import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch import optim
from tqdm import tqdm

seed = 0
torch.manual_seed(seed)


def download_pretrained_weights():
    """This function downloads the weights of our pre-trained DCGAN over 50 epochs.

    This submission fine-tunes our model that was already pre-trained on many images.
    """
    DIR = Path(__file__).parent / "models"
    DIR.mkdir(exist_ok=True)

    DIR_DISC = DIR / "discriminator_19900.pth"
    DIR_GEN = DIR / "generator_19900.pth"

    if DIR_DISC.exists() and DIR_GEN.exists():
        return
    print("Did not find pretrained weights in the directory. Starting download.")
    tmp_filename = "weights_50_epochs.zip"

    url = "https://drive.rezel.net/s/8dirwK3DLySeqz5/download/weights_50_epochs.zip"

    urllib.request.urlretrieve(url, tmp_filename)
    with zipfile.ZipFile(tmp_filename, 'r') as zip_ref:
        zip_ref.extractall(DIR)

    Path(tmp_filename).unlink()
    print("Finished downloading.")


class GeneratorGAN(nn.Module):
    def __init__(self, channels, latent, features):
        super(GeneratorGAN, self).__init__()
        self.body = nn.Sequential(
            nn.ConvTranspose2d(latent, features * 8, 4, 1, 0, bias=False),
            nn.BatchNorm2d(features * 8),
            nn.ReLU(True),

            nn.ConvTranspose2d(features * 8, features *
                               4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(features * 4),
            nn.ReLU(True),

            nn.ConvTranspose2d(features * 4, features *
                               2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(features * 2),
            nn.ReLU(True),

            nn.ConvTranspose2d(features * 2, features, 4, 2, 1, bias=False),
            nn.BatchNorm2d(features),
            nn.ReLU(True),

            nn.ConvTranspose2d(features, channels, 4, 2, 1, bias=False),
            nn.Tanh()
        )

    def forward(self, x):
        return self.body(x)


class DiscriminatorGAN(nn.Module):
    def __init__(self, channels, features):
        super(DiscriminatorGAN, self).__init__()
        self.body = nn.Sequential(
            nn.Conv2d(channels, features, 4, 2, 1, bias=False),
            nn.LeakyReLU(0.2, inplace=True),

            nn.Conv2d(features, features * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(features * 2),
            nn.LeakyReLU(0.2, inplace=True),

            nn.Conv2d(features * 2, features * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(features * 4),
            nn.LeakyReLU(0.2, inplace=True),

            nn.Conv2d(features * 4, features * 8, 4, 2, 1, bias=False),
            nn.BatchNorm2d(features * 8),
            nn.LeakyReLU(0.2, inplace=True),

            nn.Conv2d(features * 8, 1, 4, 1, 0, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x):
        return self.body(x)


class Generator:
    """
    This is a DCGAN, implemented by François.
    """

    def __init__(self, latent_space_dimension):
        """Initializes a Generator object that is used for `ramp` training and evaluation.

        This object is used to wrap your generator model and anything else required to train it and
        to generate samples.

        Args:
            latent_space_dimension (int): Dimension of the latent space, where inputs of the generator are sampled.
        """

        # We force the latent space dimension because we load a pretrained model that only
        # accounts for a latent space of dimension 100.
        self.latent_space_dimension: int = 100

        self.batch_size = 128
        self.image_size = 64
        self.channels = 3

        self.g_features = 64
        self.d_features = 64

        self.epochs = 1
        self.lr = 2e-4
        self.beta1 = 0.5

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # Models
        self.generator = GeneratorGAN(
            self.channels, self.latent_space_dimension, self.g_features).to(self.device)
        self.discriminator = DiscriminatorGAN(
            self.channels, self.d_features).to(self.device)

        # convention for labels
        self.real_label = 1.
        self.fake_label = 0.

        # Optimizers
        self.criterion = nn.BCELoss()
        self.optimizer_g = optim.Adam(
            self.generator.parameters(), lr=self.lr, betas=(self.beta1, 0.999))
        self.optimizer_d = optim.Adam(
            self.discriminator.parameters(), lr=self.lr, betas=(self.beta1, 0.999))

        download_pretrained_weights()

    # def fit(self, image_folder: torchvision.datasets.ImageFolder):
    def fit(self, batchGeneratorBuilderNoValidNy):
        """Trains the generator with a batch generator builder, which can return a Python Generator with its method `get_train_generators`.

        In this submission, this method fine-tunes the pre-trained generator and discriminator.

        Args:
            batchGeneratorBuilderNoValidNy (_type_): _description_
        """

        steps = 0

        # Load pretrained model
        PATH = Path(__file__).parent / "models"
        self.generator.load_state_dict(
            torch.load(PATH / "generator_19900.pth"))
        self.discriminator.load_state_dict(
            torch.load(PATH / "discriminator_19900.pth"))

        self.discriminator.to(self.device)

        self.generator.train()
        self.discriminator.train()

        for epoch in tqdm(np.arange(1, self.epochs + 1)):
            self.discriminator.to(self.device)
            generator_of_images, total_nb_images = batchGeneratorBuilderNoValidNy.get_train_generators(
                batch_size=self.batch_size)
            # nb_batches = total_nb_images // self.batch_size + 1 * (total_nb_images % self.batch_size !=0)
            # print(f"Epoch {epoch} of {self.epochs}.")
            # print("Total number of imgs :", total_nb_images)
            # print(f'Last epoch average discriminator loss: {results["loss_d"][-1]}.')
            # print(f'Last epoch average generator loss: {results["loss_g"][-1]}.')
            # running = {
            #     "d_real_score": 0.,
            #     "d_fake_score": 0.,
            #     "d_fake_score_2": 0.,
            #     "loss_d": 0.,
            #     "loss_g": 0.,
            # }

            for idx, batch_ in enumerate(generator_of_images):
                # (1) update the discriminator: maximize log(D(x)) + log(1 - D(G(z)))
                # all-real batch
                batch = torch.Tensor(batch_).to(self.device)
                self.discriminator.zero_grad()
                real_img = batch.to(self.device)
                batch_size = real_img.size(0)
                label = torch.full((batch_size,), self.real_label,
                                   dtype=torch.float, device=self.device)
                real_out = self.discriminator(real_img).view(-1)
                loss_real = self.criterion(real_out, label)
                loss_real.backward()

                # all-fake batch
                noise = torch.randn(
                    batch_size, self.latent_space_dimension, 1, 1, device=self.device)
                fake = self.generator(noise)
                label.fill_(self.fake_label)
                fake_out = self.discriminator(fake.detach()).view(-1)
                loss_fake = self.criterion(fake_out, label)
                loss_fake.backward()

                self.optimizer_d.step()

                d_real_score = real_out.mean().item()
                d_fake_score = fake_out.mean().item()
                loss_d = loss_real + loss_fake

                # (2) update the generator: maximize log(D(G(z)))
                self.generator.zero_grad()
                # fake labels are real for generator cost
                label.fill_(self.real_label)
                fake_out = self.discriminator(fake).view(-1)
                loss_g = self.criterion(fake_out, label)
                loss_g.backward()
                self.optimizer_g.step()

                # print("Finished step")

                # d_fake_score_2 = fake_out.mean().item()
                steps += 1

                # running["d_real_score"] += d_real_score
                # running["d_fake_score"] += d_fake_score
                # running["loss_d"] += loss_d.item()
                # running["d_fake_score_2"] += d_fake_score_2
                # running["loss_g"] += loss_g.item()

        print("Finished fine-tuning.")

    def generate(self, latent_space_noise):
        """Generates a minibatch of `nb_image` colored (3 channels : RGB) images of size 64 x 64 from a matrix in the latent space.

        Args:
            latent_space_noise (ndarray, shape (nb_image, 1024)): a mini-batch of noise of dimension  issued from a normal (Gaussian) distribution

        Returns:
            ndarray, shape (nb_image, 3, 64, 64): A mini-batch of `nb_image` colored (3 channels : RGB) images of size 64 x 64 from a matrix in the latent space.
        """
        # nb_image = latent_space_noise.shape[0]

        self.generator.eval()
        self.discriminator.eval()

        # In order to save space on the GPU, we send the discriminator back to the CPU.
        self.discriminator.to("cpu")

        with torch.no_grad():
            # We take a noise of size [nb_image, latent_size, 1, 1] for our generator.
            truncated_noise = torch.Tensor(
                latent_space_noise[:, :self.latent_space_dimension, np.newaxis, np.newaxis]).to(self.device)
            batch = self.generator(truncated_noise)

        return batch.numpy(force=True)
