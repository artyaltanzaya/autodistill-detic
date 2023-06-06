import argparse
import multiprocessing as mp
import os
import subprocess
import sys
from dataclasses import dataclass

import numpy as np
import supervision as sv
import torch
from autodistill.detection import CaptionOntology, DetectionBaseModel
from detectron2.config import get_cfg
from detectron2.data.detection_utils import read_image
from detectron2.utils.logger import setup_logger

VOCAB = "custom"
CONFIDENCE_THRESHOLD = 0.1


def setup_cfg(args):
    from centernet.config import add_centernet_config
    from detic.config import add_detic_config

    cfg = get_cfg()
    cfg.MODEL.DEVICE = "cpu" if args.cpu else "cuda"
    add_centernet_config(cfg)
    add_detic_config(cfg)
    cfg.merge_from_file(args.config_file)
    cfg.merge_from_list(args.opts)
    # Set score_threshold for builtin models
    cfg.MODEL.RETINANET.SCORE_THRESH_TEST = CONFIDENCE_THRESHOLD
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = CONFIDENCE_THRESHOLD
    cfg.MODEL.PANOPTIC_FPN.COMBINE.INSTANCES_CONFIDENCE_THRESH = CONFIDENCE_THRESHOLD
    cfg.MODEL.ROI_BOX_HEAD.ZEROSHOT_WEIGHT_PATH = "rand"  # load later
    if not args.pred_all_class:
        cfg.MODEL.ROI_HEADS.ONE_CLASS_PER_PROPOSAL = False
    cfg.freeze()
    return cfg


def load_detic_model(ontology):
    mp.set_start_method("spawn", force=True)
    setup_logger(name="fvcore")
    logger = setup_logger()

    args = argparse.Namespace()

    args.confidence_threshold = CONFIDENCE_THRESHOLD
    args.vocabulary = VOCAB
    args.opts = []
    args.config_file = "configs/Detic_LCOCOI21k_CLIP_SwinB_896b32_4x_ft4x_max-size.yaml"
    args.cpu = False if torch.cuda.is_available() else True
    args.opts.append("MODEL.WEIGHTS")
    args.opts.append("models/Detic_LCOCOI21k_CLIP_SwinB_896b32_4x_ft4x_max-size.pth")
    args.output = None
    args.webcam = None
    args.video_input = None
    print(ontology.prompts())
    args.custom_vocabulary = ", ".join(ontology.prompts()).rstrip(",")
    print(args.custom_vocabulary)
    args.pred_all_class = True
    cfg = setup_cfg(args)

    from detic.predictor import VisualizationDemo

    demo = VisualizationDemo(cfg, args)

    return demo


HOME = os.path.expanduser("~")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

installation_commands = [
    "mkdir ~/.cache/autodistill/",
    "cd ~/.cache/autodistill/",
    "git clone git@github.com:facebookresearch/detectron2.git",
    "cd detectron2",
    "pip install -e .",
    "pip install -r requirements.txt",
    "cd ..",
    "git clone https://github.com/facebookresearch/Detic.git --recurse-submodules",
    "cd Detic",
    "pip install -r requirements.txt",
    "mkdir models",
    "wget https://dl.fbaipublicfiles.com/detic/Detic_LCOCOI21k_CLIP_SwinB_896b32_4x_ft4x_max-size.pth -O models/Detic_LCOCOI21k_CLIP_SwinB_896b32_4x_ft4x_max-size.pth",
]


def install_detic():
    for command in installation_commands:
        # keep track of pathi
        if command.startswith("cd"):
            # re[place ~ with home]
            command = command.replace("~", HOME)
            os.chdir(command.split(" ")[1])

        subprocess.run(command, shell=True)


@dataclass
class DETIC(DetectionBaseModel):
    ontology: CaptionOntology

    def __init__(self, ontology: CaptionOntology):
        self.ontology = ontology
        if not os.path.exists(HOME + "/.cache/autodistill/detectron2"):
            install_detic()

        original_dir = os.getcwd()

        sys.path.insert(0, HOME + "/.cache/autodistill/Detic/third_party/CenterNet2/")

        sys.path.insert(0, HOME + "/.cache/autodistill/Detic/")
        os.chdir(HOME + "/.cache/autodistill/Detic/")

        self.detic_model = load_detic_model(ontology)

        # change back to original directory
        os.chdir(original_dir)

    def predict(self, input: str) -> sv.Detections:
        labels = self.ontology.prompts()

        img = read_image(input, format="BGR")

        predictions, visualized_output = self.detic_model.run_on_image(img)
        import cv2

        cv2.imshow("test", visualized_output.get_image()[:, :, ::-1])
        cv2.waitKey(0)
        exit()
        pred_boxes = predictions["instances"].pred_boxes.tensor.cpu().numpy()
        pred_classes = predictions["instances"].pred_classes.cpu().numpy()
        pred_scores = predictions["instances"].scores.cpu().numpy()

        # filter out predictions that are not in the ontology
        final_pred_boxes = []
        final_pred_classes = []
        final_pred_scores = []

        # get class labels

        print(predictions)

        for i, pred_class in enumerate(pred_classes):
            if labels[pred_class] in labels:
                final_pred_boxes.append(pred_boxes[i])
                final_pred_classes.append(pred_class)
                final_pred_scores.append(pred_scores[i])

        if len(final_pred_classes) == 0:
            return sv.Detections.empty()

        return sv.Detections(
            xyxy=np.array(final_pred_boxes),
            class_id=np.array(final_pred_classes),
            confidence=np.array(final_pred_scores),
        )
