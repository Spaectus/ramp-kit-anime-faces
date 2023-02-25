from collections import Counter

from torch.utils.data import DataLoader, Dataset
from torchvision.transforms import Compose, ToTensor

import torch
from torchmetrics.image.fid import FrechetInceptionDistance
from torchmetrics.image.kid import KernelInceptionDistance
from torchmetrics.image.inception import InceptionScore
from rampwf.score_types.base import BaseScoreType

import numpy as np
import matplotlib.pyplot as plt
import torchvision.utils as vutils

from PIL import Image

import gc

import warnings


def disable_torchmetrics_warnings():
    """This function disables the warnings due to initializing KernelInceptionDistance objects from torchmetrics.image.kid.
    """
    warnings.resetwarnings()
    warnings.filterwarnings('ignore')  # Ignore everything
    # ignore everything does not work: ignore specific messages, using regex
    warnings.filterwarnings(
        'ignore', '.*UserWarning: Metric `Kernel Inception Distance`*')


disable_torchmetrics_warnings()

device = "cuda" if torch.cuda.is_available() else "cpu"

transform = Compose([
    ToTensor(),
])


class ImageSet(Dataset):
    """This class inherits from the Dataset class of PyTorch and is used to load the images locally with the paths of the images.

    The images are already transformed beforehand, so all there is to do in order to feed them to the metrics is to send them in
    minibatches using a DataLoader object.
    """

    def __init__(self, paths, transform, preload=False):
        """Initializes the dataset from a tuple of paths.

        Args:
            paths (tuple of `str` objects): tuple of strings containing the paths of the images used in the Dataset.
            transform (Compose): A composition of transforms to be applied on the images.
            preload (bool, optional): A boolean to indicate whether the images are preloaded as PyTorch Tensor objects. Defaults to False.
        """
        self.paths = paths
        self.transform = transform
        self.preload = preload
        if self.preload:
            self.files = [
                self.transform(
                    Image.open(path)
                ) for path in self.paths]

    def __getitem__(self, index):
        """Gets an item from the dataset.

        Args:
            index (int): The index of the image in the dataset.

        Returns:
            Tensor: the `index`-th image in the dataset.
        """
        if self.preload:
            return self.files[index]
        else:
            return self.transform(
                Image.open(self.paths[index])
            )

    def __len__(self):
        """Returns the number of images in the dataset.

        Returns:
            int: The number of images in the dataset.
        """
        return len(self.paths)


class Master():
    """A class that centralizes the computations of the metrics for `ramp-test`.

    Since images are generated by batch from the competitor's generator, a Python generator is used to retrieve the
    batches of images, which are then fed to the `Torchvision` objects to compute the metrics.

    In order to ensure that we only do one pass of each generated dataset, multiple metrics are computed at the same time, from
    the local fold metric to the bagged metrics that account for all the train sets.

    This class centralizes the computations of the metrics, which are then retrieved by the BaseScoreType objects of
    `ramp-workflow`.
    """
    def __init__(self, n_fold=3):
        """Initializes a Master object to centralize the computation of metrics.

        The object locally stores a FrechetInceptionDistance object, a KernelInceptionDistance and a InceptionScore object to
        compute the bagged metrics after making a full pass on each generated set.

        In order to keep track of the current fold, `memory` keeps track of the current fold for the metrics to compute.

        Args:
            n_fold (int, optional): The number of folds to perform for evaluation. Defaults to 3.
        """
        self.batch_size = 32
        self.score = {}
        # [None, 0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 3, 3, 3]
        self.pattern = [
            None] + [i for i in range(n_fold) for z in range(3)] + 50 * [n_fold]  # 6
        self.memory_call = Counter()
        self.memory = Counter()
        self.n_fold = n_fold
        
        # Permanent metrics to compute bagged scores
        self.fid = FrechetInceptionDistance(
            reset_real_features=True, normalize=True).to(device)
        self.kid = KernelInceptionDistance(
            reset_real_features=True, normalize=True).to(device)
        self.is_ = InceptionScore(normalize=True).to(device)

        # For plotting
        self.displayed = None

    def eval(self, y_true, y_pred, metric):
        """Evaluates scores for a given metric on a certain fold.

        If no score has been computed for the current fold before, the method will compute all the scores for the current fold
        and store them in `self.scores`.

        This allows the metrics to be computed with a single pass on each generated set.

        Args:
            y_true (tuple of `str` objects): The paths of the images that are used as real images.
            y_pred (Generator): A Python generator object that yields mini-batches of generated samples as numpy arrays.
            metric (str): {"FID", "KID_mean", "KID_std", "IS_mean", "IS_std"} The name of the metric to compute.

        Returns:
            float: The value of the metric to compute for the current fold.
        """
        
        assert metric in ("FID", "KID_mean", "KID_std", "IS_mean", "IS_std")
        self.memory_call[metric] += 1
        # retrieve position in k_fold
        current_fold: int = self.pattern[self.memory_call[metric]]
        context = (metric, current_fold)
        self.memory[context] += 1  # we count the number of call of each metric

        if context in self.score:
            # We have already compute this metric for this fold
            return self.score[context]

        if current_fold == self.n_fold:
            # Compute the permanent metrics that received a full pass of each dataset and generated dataset.

            fid_score = self.fid.compute().item()
            self.score[("FID", current_fold)] = fid_score

            kid_mean, kid_std = self.kid.compute()
            # We rescale the KID scores because otherwise they are too small and too close to 0.
            self.score[("KID_mean", current_fold)] = kid_mean.item()*1000
            self.score[("KID_std", current_fold)] = kid_std.item()*1000

            is_mean, is_std = self.is_.compute()
            self.score[("IS_mean", current_fold)] = is_mean.item()*1000
            self.score[("IS_std", current_fold)] = is_std.item()*1000

            return self.score[context]

        if len(y_true) == 0:
            # assert self.memory[metric] == 3
            # print(f"len(y_true) == 0 and {self.memory[context]=}")
            return self.score[context]

        fid = FrechetInceptionDistance(
            reset_real_features=True, normalize=True).to(device)
        kid = KernelInceptionDistance(
            reset_real_features=True, normalize=True).to(device)
        is_ = InceptionScore(normalize=True).to(device)

        i = -1
        # Handling generated data
        for i, batch in enumerate(y_pred):

            batch_ = torch.Tensor(batch).to(device)

            if i == 0:
                self.displayed = vutils.make_grid(batch_, padding=2, normalize=True).cpu()
                if not self.displayed is None:
                    plt.figure(figsize=(8, 8))
                    plt.axis("off")
                    plt.title("Generated Images")
                    plt.imshow(np.transpose(self.displayed, (1,2,0)))
                    print("The first batch of images is displayed on a different window. Please close it to continue evaluation.")
                    plt.show()



            fid.update(batch_, real=False)
            kid.update(batch_, real=False)
            is_.update(batch_)
            self.fid.update(batch_, real=False)
            self.kid.update(batch_, real=False)
            self.is_.update(batch_)



        if i == -1:
            # assert self.memory[metric] == 2
            # print(f"i==-1 and {self.memory[context]=}")
            return self.score[context]

        # Handling true data
        folders = set(path_.parent.name for path_ in y_true)
        assert len(folders) == 3
        y_true_ = tuple(
            path for path in y_true if path.parent.name == f"train_{current_fold+1}")

        dataset = ImageSet(
            paths=y_true_,
            transform=transform,
            preload=True,
        )
        loader = DataLoader(
            dataset,
            batch_size=self.batch_size,
            shuffle=False
        )
        for batch in loader:
            batch_ = batch.to(device)
            fid.update(batch_, real=True)
            kid.update(batch_, real=True)
            self.fid.update(batch_, real=True)
            self.kid.update(batch_, real=True)

        fid_score = fid.compute().item()
        self.score[("FID", current_fold)] = fid_score

        kid_mean, kid_std = kid.compute()
        # We rescale the KID scores because otherwise they are too small and too close to 0.
        self.score[("KID_mean", current_fold)] = kid_mean.item()*1000
        self.score[("KID_std", current_fold)] = kid_std.item()*1000

        is_mean, is_std = is_.compute()
        self.score[("IS_mean", current_fold)] = is_mean.item()*1000
        self.score[("IS_std", current_fold)] = is_std.item()*1000

        # Delete models to make some space on the GPU.
        del fid, kid, is_
        torch.cuda.empty_cache()
        gc.collect()

        return self.score[context]


MASTER = Master()

# Fréchet Inception Distance (FID)

class FID(BaseScoreType):
    precision = 1

    def __init__(self, name="FID"):
        self.name = name

    def check_y_pred_dimensions(self, y_true, y_pred):
        pass

    def __call__(self, y_true, y_pred):
        assert isinstance(y_true, tuple)
        return MASTER.eval(y_true, y_pred, metric="FID")


# Kernel Inception Distance (KID)

class KIDMean(BaseScoreType):
    precision = 1

    def __init__(self, name="KID_mean"):
        self.name = name

    def check_y_pred_dimensions(self, y_true, y_pred):
        pass

    def __call__(self, y_true, y_pred):
        assert isinstance(y_true, tuple)
        return MASTER.eval(y_true, y_pred, metric="KID_mean")


class KIDStd(BaseScoreType):
    precision = 1

    def __init__(self, name="KID_std"):
        self.name = name

    def check_y_pred_dimensions(self, y_true, y_pred):
        pass

    def __call__(self, y_true, y_pred):
        assert isinstance(y_true, tuple)
        return MASTER.eval(y_true, y_pred, metric="KID_std")


# Inception Score (IS)

class ISMean(BaseScoreType):
    precision = 1

    def __init__(self, name="IS_mean"):
        self.name = name

    def check_y_pred_dimensions(self, y_true, y_pred):
        pass

    def __call__(self, y_true, y_pred):
        assert isinstance(y_true, tuple)
        return MASTER.eval(y_true, y_pred, metric="IS_mean")


class ISStd(BaseScoreType):
    precision = 1

    def __init__(self, name="IS_std"):
        self.name = name

    def check_y_pred_dimensions(self, y_true, y_pred):
        pass

    def __call__(self, y_true, y_pred):
        assert isinstance(y_true, tuple)
        return MASTER.eval(y_true, y_pred, metric="IS_std")
