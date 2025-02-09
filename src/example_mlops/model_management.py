"""Commands for model management."""

import operator
import os

import click
import onnx
import onnxruntime as ort
import torch
from dotenv import load_dotenv
from neural_compressor.config import AccuracyCriterion, PostTrainingQuantConfig
from neural_compressor.quantization import fit
from torchmetrics.classification import MulticlassAccuracy

import wandb
from example_mlops.data import MnistDataModule
from example_mlops.model import load_from_checkpoint
from example_mlops.utils import HydraRichLogger

load_dotenv()
logger = HydraRichLogger(level=os.getenv("LOG_LEVEL", "INFO"))


@click.group()
def cli():
    """Commands for model management."""
    pass


@click.command()
@click.argument("model-name")
@click.option("--metric_name", default="accuracy", help="Name of the metric to choose the best model from.")
@click.option("--higher-is-better", default=True, help="Whether higher metric values are better.")
def stage_best_model_to_registry(model_name, metric_name, higher_is_better):
    """
    Stage the best model to the model registry.

    Args:
        model_name: Name of the model to be registered.
        metric_name: Name of the metric to choose the best model from.
        higher_is_better: Whether higher metric values are better.

    """
    api = wandb.Api(
        api_key=os.getenv("WANDB_API_KEY"),
        overrides={"entity": os.getenv("WANDB_ENTITY"), "project": os.getenv("WANDB_PROJECT")},
    )
    artifact_collection = api.artifact_collection(type_name="model", name=model_name)

    best_metric = float("-inf") if higher_is_better else float("inf")
    compare_op = operator.gt if higher_is_better else operator.lt
    best_artifact = None
    for artifact in list(artifact_collection.artifacts()):
        if metric_name in artifact.metadata and compare_op(artifact.metadata[metric_name], best_metric):
            best_metric = artifact.metadata[metric_name]
            best_artifact = artifact

    if best_artifact is None:
        logger.error("No model found in registry.")
        return

    logger.info(f"Best model found in registry: {best_artifact.name} with {metric_name}={best_metric}")
    best_artifact.link(
        target_path=f"{os.getenv('WANDB_ENTITY')}/model-registry/{model_name}", aliases=["best", "staging"]
    )
    best_artifact.save()
    logger.info("Model staged to registry.")


@click.command()
@click.argument("model-name")
@click.option("--aliases", "-a", multiple=True, default=["staging"], help="List of aliases to link the artifact with.")
def link_latest_model(model_name: str, aliases: list[str]):
    """Link the latest model to the model registry."""
    api = wandb.Api(
        api_key=os.getenv("WANDB_API_KEY"),
        overrides={"entity": os.getenv("WANDB_ENTITY"), "project": os.getenv("WANDB_PROJECT")},
    )
    artifact_collection = api.artifact_collection(type_name="model", name=model_name)
    for artifact in list(artifact_collection.artifacts()):
        if "latest" in artifact.aliases:
            artifact.link(target_path=f"{os.getenv('WANDB_ENTITY')}/model-registry/{model_name}", aliases=aliases)
            artifact.save()
            logger.info("Model linked to registry.")
            return


@click.command()
@click.argument("model-name")
def print_latest_model(model_name: str) -> None:
    """Print the latest model in the model registry."""
    api = wandb.Api(
        api_key=os.getenv("WANDB_API_KEY"),
        overrides={"entity": os.getenv("WANDB_ENTITY"), "project": os.getenv("WANDB_PROJECT")},
    )
    artifact_collection = api.artifact_collection(type_name="model", name=model_name)
    for artifact in list(artifact_collection.artifacts()):
        if "latest" in artifact.aliases:
            print(artifact.name)


@click.command()
@click.argument("artifact-path")
@click.option("--aliases", "-a", multiple=True, default=["staging"], help="List of aliases to link the artifact with.")
def link_model(artifact_path: str, aliases: list[str]) -> None:
    """
    Stage a specific model to the model registry.

    Args:
        artifact_path: Path to the artifact to stage.
            Should be of the format "entity/project/artifact_name:version".
        aliases: List of aliases to link the artifact with.

    Example:
        model_management link-model entity/project/artifact_name:version -a staging -a best

    """
    if artifact_path == "":
        logger.error("Please provide artifact_path")
        return

    api = wandb.Api(
        api_key=os.getenv("WANDB_API_KEY"),
        overrides={"entity": os.getenv("WANDB_ENTITY"), "project": os.getenv("WANDB_PROJECT")},
    )
    _, _, artifact_name_version = artifact_path.split("/")
    artifact_name, _ = artifact_name_version.split(":")

    artifact = api.artifact(artifact_path)
    artifact.link(target_path=f"{os.getenv('WANDB_ENTITY')}/model-registry/{artifact_name}", aliases=aliases)
    artifact.save()
    logger.info("Model linked to registry.")
    logger.info()


@click.command()
@click.argument("artifact-path")
def export_and_quantize(artifact_path: str) -> None:
    """Export a given model artifact to ONNX format."""
    if artifact_path == "":
        logger.error("Please provide artifact_path")
        return

    model = load_from_checkpoint(artifact_path)
    model.to_onnx(
        "models/checkpoint.onnx",
        input_sample=model.input_sample.to(model.device),
        input_names=["image"],
        dynamic_axes={"image": {0: "batch_size"}},
    )
    logger.info("Model exported to ONNX format at models/checkpoint.onnx")

    onnx_model = onnx.load("models/checkpoint.onnx")

    datamodule = MnistDataModule()
    datamodule.setup("fit")
    val_dataloader = datamodule.val_dataloader()

    def eval_func(onnx_model):
        """Evaluate the model on the validation set."""
        metric = MulticlassAccuracy(num_classes=10, average="micro")
        sess = ort.InferenceSession(onnx_model.SerializeToString())
        for input_data, label in val_dataloader:
            output = sess.run(None, {"image": input_data.numpy()})
            metric.update(torch.tensor(output[0]), label)
        return metric.compute().item()

    config = PostTrainingQuantConfig(
        backend="default",
        accuracy_criterion=AccuracyCriterion(higher_is_better=True, criterion="relative", tolerable_loss=0.01),
    )
    quantized_model = fit(onnx_model, conf=config, calib_dataloader=val_dataloader, eval_func=eval_func)
    quantized_model.save("models/checkpoint_quantized.onnx")
    logger.info("Model quantized and saved to models/checkpoint_quantized.onnx")

    original_size = os.path.getsize("models/checkpoint.onnx") / (1024 * 1024)
    quantized_size = os.path.getsize("models/checkpoint_quantized.onnx") / (1024 * 1024)
    logger.info(f"Original model size: {original_size:.2f} MB")
    logger.info(f"Quantized model size: {quantized_size:.2f} MB")


cli.add_command(stage_best_model_to_registry)
cli.add_command(link_latest_model)
cli.add_command(link_model)
cli.add_command(print_latest_model)
cli.add_command(export_and_quantize)


if __name__ == "__main__":
    cli()
