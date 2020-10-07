from fastai.vision.all import *
import random
RANDOM_SEED = 42  # random.randint(1,100)
DATASET_PATH = Path('/Users/tomcounsell/src/ai/datasets/dogs/images/')
EXAMPLE_IMAGE_FILEPATH = DATASET_PATH/'n02085782-Japanese_spaniel/n02085782_50.jpg'
AI_MODEL_FILENAME = 'model_export.pkl'

import re
def parse_breed_name(folder_name):
    return re.findall('.+/n\d+-([\w-]+)$', folder_name)[0].replace("_", " ").title()

def parse_ncode(folder_name):
    return re.findall('.+/(n\d+)-[\w-]+$', folder_name)[0]

BREED_NAMES = {parse_ncode(str(sub_dir)): parse_breed_name(str(sub_dir)) for sub_dir in DATASET_PATH.ls()}
# print(f"Some dog breeds: {list(BREED_NAMES.values())[:5]}")

def get_breed_from_filename(filename):
    return BREED_NAMES[re.findall('(n\d+).+.jpg$', str(filename))[0]]

Path.BASE_PATH = DATASET_PATH

dogs = DataBlock(
    blocks=(ImageBlock, CategoryBlock),
    get_items=get_image_files,
    splitter=RandomSplitter(seed=RANDOM_SEED),
    get_y=get_breed_from_filename,
    item_tfms=Resize(460),
    batch_tfms=aug_transforms(size=224, min_scale=0.75)
)
dataloaders = dogs.dataloaders(DATASET_PATH)
# dataloaders.show_batch(nrows=2, ncols=4)

from fastai.callback.fp16 import *
learn = cnn_learner(dataloaders, resnet50, metrics=error_rate).to_fp16()
learn.fine_tune(6, freeze_epochs=3)

learn.export(AI_MODEL_FILENAME)

# # LOAD AND USE LATER WITH
# learn = load_learner(AI_MODEL_FILENAME)
# customer_photo = PILImage.create('images/some_dog.jpg')
# breed,_,probs = learn.predict(customer_photo)
# print(f"{probs[1].item():.2f} sure this is a {breed}")
