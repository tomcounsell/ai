import numpy as np
from PIL import Image


def zoom_and_crop(
    image: Image,
    zoom: float = 0,
    angle_from_center: float = 0,
    distance_from_center: float = 0,
    is_orig_image_landscape=True,
    resize_dimensions=(100, 100),
):
    # suggest adjusting zoom (from 0->1) or distance_from_center (from 0->1), not both too much together
    # angle_from_center should be random between 0 and 2*pi

    shorter_edge = (
        image.height if is_orig_image_landscape else (min([image.height, image.width]))
    )
    center_x, center_y = image.width / 2, image.height / 2

    if distance_from_center:
        distance_pixels_x = distance_from_center * 0.8 * image.width / 2
        distance_pixels_y = distance_from_center * 0.8 * image.height / 2

        center_x += np.cos(angle_from_center) * distance_pixels_x
        center_y += np.sin(angle_from_center) * distance_pixels_y

        shorter_edge = (
            min([center_x, center_y, image.width - center_x, image.height - center_y])
            * 2
        )

    # zoom values (from 0 -> 1) roughly translate to (100% -> 1%) of image data
    zoom_pixels_from_center = int((shorter_edge / np.exp(zoom * 4)) / 2)
    center_x, center_y = int(center_x), int(center_y)

    (x1, y1, x2, y2) = (
        center_x - zoom_pixels_from_center,
        center_y - zoom_pixels_from_center,
        center_x + zoom_pixels_from_center,
        center_y + zoom_pixels_from_center,
    )

    # image = image.crop((x1, y1, x2, y2))  # redundant
    return image.resize(resize_dimensions, box=(x1, y1, x2, y2))


def add_random_noise(image, strength=0):
    if not strength:
        return image
    # do something
    return image


def add_random_compression(image, random_seed=None):
    if not random_seed:
        return image
    # do something
    return image
