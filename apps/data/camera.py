import logging

import cv2
from PIL import Image

from apps.data.data_source import DataSource

logger = logging.getLogger(__name__)


class Camera(DataSource):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def __enter__(self):
        self.camera = cv2.VideoCapture(0)  # turn on webcam
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.camera.release()
        cv2.destroyAllWindows()

    def get_data_sample(self, in_color: bool = False):
        import time

        time.sleep(2)
        ret, frame = self.camera.read()
        if in_color:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

    def get_image_sample(self, in_color: bool = False):
        if not isinstance(self.sample_image, Image):
            self.sample_image = Image.fromarray(self.get_data_sample(in_color=in_color))
        return self.sample_image

    def save_sample(self, in_color: bool = False, filename: str = "sample.jpg"):
        image = self.get_image_sample(in_color=in_color)
        with open(f"static/temp/{filename}", "wb") as file:
            image.save(file)
        print(
            f"{'color' if in_color else 'greyscale'} image save to static/temp/{filename}"
        )

    # def save_sample_on_key(self, keystroke='q', filename='capture.jpg'):
    #     while True:
    #         ret, frame = self.camera.read()
    #         # rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    #         # cv2.imshow('frame', rgb)
    #
    #         if cv2.waitKey(1) & 0xFF == ord('q'):
    #             out = cv2.imwrite(filename, frame)
    #             break

    def get_frame(self, *args, **kwargs):
        ret = False
        while not ret:
            ret, frame = self.camera.read()
        return frame
