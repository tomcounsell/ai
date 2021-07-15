from abc import ABC

from apps.common.utilities.compression.image_compresssion import crop_image


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
        # publish via stimulus
        # it should do something with image like pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))



class Stimulus(ABC):
    data = bytes()
    params = {}

    def __init__(self, source: DataSource, raw_input: bytes):
        self.source = source


    def publish(self):
        # open kafka channel, push self.data
        pass


class Vision(Stimulus):
    source: Camera = None

    def __init__(self, source: DataSource, raw_input: bytes):
        super().__init__(source, raw_input)
        image = source.image
        # stimulus can have increased zoom (0->1) or distance_from_center (0->1), not both
        # angle_from_center should be random between 0 and 2*pi
        zoom, angle_from_center, distance_from_center = self.params
        self.data = bytes(crop_image(image, zoom, angle_from_center, distance_from_center))


# class Motor(Stimulus):
#     source: Muscle = None

# class Prediction(Stimulus):
#     source: AgentPrediction = None

