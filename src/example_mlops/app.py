"""API for the MNIST model."""

import os
from contextlib import asynccontextmanager
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, File
from PIL import Image
from torchvision.transforms.v2.functional import pil_to_tensor

from example_mlops.data import default_img_transform
from example_mlops.model import load_from_checkpoint
from example_mlops.utils import HydraRichLogger

load_dotenv()

logger = HydraRichLogger(level=os.getenv("LOG_LEVEL", "INFO"))

models = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model at startup and clean up at shutdown."""
    logger.info(f"Loading model from {os.getenv('MODEL_CHECKPOINT')}...")
    if os.getenv("MODEL_CHECKPOINT") is None:
        logger.error("No model checkpoint found.")
        exit(1)

    mnist_model = load_from_checkpoint(os.getenv("MODEL_CHECKPOINT"), logdir="models")
    models["mnist"] = mnist_model
    logger.info("Model loaded.")

    yield  # Wait for the application to finish

    logger.info("Cleaning up...")
    del mnist_model


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    """Root endpoint of the API."""
    return {"message": "Welcome to the MNIST model inference API!"}


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/modelstats")
def modelstats():
    """Return model information."""
    return {
        "model architecture": str(models["mnist"]),
    }


@app.post("/predict")
async def predict(image: bytes = File(...)):
    """Predict the label of a given image."""
    pil_image = Image.open(BytesIO(image))
    image_data = pil_to_tensor(pil_image)
    logger.info("Image loaded.")
    input_tensor = default_img_transform(image_data)
    probs, preds = models["mnist"].inference(input_tensor)

    # Return the predicted label
    return {"prediction": int(preds[0]), "probabilities": probs[0].tolist()}
