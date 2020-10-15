from abc import ABC
from pathlib import Path
import re
import random
from fastai.vision.all import *
from fastai.callback.fp16 import *


class DogBreedsData(ABC):
    BREED_NAMES = {}

    def __init__(self, dataset_path):
        self.BREED_NAMES = {
            self.parse_ncode(str(sub_dir)): self.parse_breed_name(str(sub_dir))
            for sub_dir in dataset_path.ls()
        }

    def parse_breed_name(self, folder_name):
        return re.findall('.+/n\d+-([\w-]+)$', folder_name)[0].replace("_", " ").title()

    def parse_ncode(self, folder_name):
        return re.findall('.+/(n\d+)-[\w-]+$', folder_name)[0]

    def get_breed_from_filename(self, filename):
        return self.BREED_NAMES[re.findall('(n\d+).+.jpg$', str(filename))[0]]

    def get_method_for_get_breed_from_filename(self, breed_names=BREED_NAMES):
        def get_breed_from_filename(self, filename):
            return breed_names[re.findall('(n\d+).+.jpg$', str(filename))[0]]


class DogBreedsNN(ABC):

    RANDOM_SEED = 42  # random.randint(1,100)
    AI_MODEL_FILENAME = 'model_export.pkl'
    SHOW_FIGURES = False
    DATASET_PATH = None

    def setup(self):  # set show_figures True if running in Jupyter nb
        dog_breeds_data = DogBreedsData(self.DATASET_PATH)

        if self.SHOW_FIGURES:
            print(f"Some dog breeds: {list(dog_breeds_data.BREED_NAMES.values())[:5]}")

        dogs = DataBlock(
            blocks=(ImageBlock, CategoryBlock),
            get_items=get_image_files,
            splitter=RandomSplitter(seed=self.RANDOM_SEED),
            get_y=dog_breeds_data.get_method_for_get_breed_from_filename(),
            item_tfms=Resize(460),
            batch_tfms=aug_transforms(size=224, min_scale=0.75)
        )
        dataloaders = dogs.dataloaders(self.DATASET_PATH)
        if self.SHOW_FIGURES:
            dataloaders.show_batch(nrows=2, ncols=4)
        self.learn = cnn_learner(dataloaders, resnet50, metrics=error_rate).to_fp16()


    def run(self):
        # self.learn.fine_tune(6, freeze_epochs=3)
        self.learn.fine_tune(1, freeze_epochs=1)

    def save(self):
        self.learn.export(self.AI_MODEL_FILENAME, pickle_protocol=2)

    def load_learner(self, alt_file_path=""):
        return load_learner(alt_file_path or self.AI_MODEL_FILENAME)

    def test(self, image_path):  # pass 'images/some_dog.jpg'
        self.learn = self.load_learner()
        customer_photo = PILImage.create(image_path)
        breed,_,probs = self.learn.predict(customer_photo)
        print(f"{probs[1].item():.2f} sure this is a {breed}")


if __name__ == "__main__":
    local_machine = False

    dog_breeds_nn = DogBreedsNN()

    if local_machine:
        dog_breeds_nn.DATASET_PATH = Path('/Users/tomcounsell/src/ai/datasets/dogs/images/')
    else:
        # !curl -o ./images.tar "http://vision.stanford.edu/aditya86/ImageNetDogs/images.tar"
        # !tar -xf images.tar
        dog_breeds_nn.DATASET_PATH = Path('Images')


    dog_breeds_nn.SHOW_FIGURES = True
    Path.BASE_PATH = dog_breeds_nn.DATASET_PATH
    print(dog_breeds_nn.DATASET_PATH)
    dog_breeds_nn.AI_MODEL_FILENAME = 'dog_breeds_nn_model_export.pkl'

    dog_breeds_nn.setup()
    dog_breeds_nn.run()

    # dog_breeds_nn.save()
    dog_breeds_nn.EXAMPLE_IMAGE_FILEPATH = dog_breeds_nn.DATASET_PATH / 'n02085782-Japanese_spaniel/n02085782_50.jpg'
    # dog_breeds_nn.test()
