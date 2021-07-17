import logging
from abc import ABC
import cv2
from PIL import Image

logger = logging.getLogger(__name__)


class DataSource(ABC):
    compression_algorithm = None

    def __init__(self, *args, **kwargs):
        pass

    def publish(self, *args, **kwargs):
        pass


class Camera(DataSource):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __enter__(self):
        self.camera = cv2.VideoCapture(0)  # turn on webcam
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.camera.release()
        cv2.destroyAllWindows()

    def save_sample(self, in_color: bool = False, filename: str = 'sample.jpg'):
        import time
        time.sleep(2)
        ret, frame = self.camera.read()

        if in_color:
            rgb_array = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb_array)
        else:
            greyscale_array = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            # greyscale_array is numpy.array
            image = Image.fromarray(greyscale_array)

        with open(f"static/temp/{filename}", 'wb') as file:
            image.save(file)
        print(f"{'color' if in_color else 'greyscale'} image save to static/temp/{filename}")


    # def save_sample_on_key(self, keystroke='q', filename='capture.jpg'):
    #     while True:
    #         ret, frame = self.camera.read()
    #         # rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    #         # cv2.imshow('frame', rgb)
    #
    #         if cv2.waitKey(1) & 0xFF == ord('q'):
    #             out = cv2.imwrite(filename, frame)
    #             break

    def stream(self):
        while True:
            ret, frame = self.camera.read()
            if not ret:
                break

        # add noise, so agents learn in a more analog style
        # publish via stimulus
        # it should do something with image like pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


class Muscle(DataSource):
    pass


class AgentPrediction(DataSource):
    pass
