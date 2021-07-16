import logging
from abc import ABC

logger = logging.getLogger(__name__)


class DataSource(ABC):
    compression_algorithm = None

    def publish(self):
        pass


class Camera(DataSource):
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(0)  # /dev/video0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # add noise, so agents learn in a more analog style
        # publish via stimulus
        # it should do something with image like pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


class Muscle(DataSource):
    pass

class AgentPrediction(DataSource):
    pass
