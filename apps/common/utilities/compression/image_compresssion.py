import numpy as np
from PIL import Image


def crop_image(image, zoom: float = 0, angle_from_center: float = 0, distance_from_center: float = 0,
               landscape_orientation=True):
    # suggest adjusting zoom (from 0->1) or distance_from_center (from 0->1), not both too much together
    # angle_from_center should be random between 0 and 2*pi

    try:
        img = Image.fromarray(image)
    except AttributeError:
        img = Image.open(image)

    shorter_edge = (img.height if landscape_orientation else (min([img.height, img.width])))
    center_x, center_y = img.width / 2, img.height / 2

    if distance_from_center:
        distance_pixels_x = distance_from_center * 0.8 * img.width / 2
        distance_pixels_y = distance_from_center * 0.8 * img.height / 2

        center_x += np.cos(angle_from_center) * distance_pixels_x
        center_y += np.sin(angle_from_center) * distance_pixels_y

        shorter_edge = min([center_x, center_y, img.width - center_x, img.height - center_y])*2

    # zoom values (from 0 -> 1) roughly translate to (100% -> 1%) of image data
    zoom_pixels_from_center = int((shorter_edge / np.exp(zoom*4)) / 2)
    center_x, center_y = int(center_x), int(center_y)

    (x1, y1, x2, y2) = (center_x - zoom_pixels_from_center, center_y - zoom_pixels_from_center,
                        center_x + zoom_pixels_from_center, center_y + zoom_pixels_from_center)

    cropped_img = img.crop((x1, y1, x2, y2))
    # img.resize()  # compress
    return cropped_img
