import torch
import torchmetrics
import torchmetrics.classification
from pytorch_lightning import LightningModule
from torch import nn
from torchvision import models


class MnistClassifier(LightningModule):
    """My awesome model."""

    def __init__(self, backbone: str) -> None:
        """Initialize model and metrics."""
        super().__init__()
        self.save_hyperparameters(logger=False)

        if backbone not in models.list_models():
            raise ValueError(f"Backbone {backbone} not available.")
        self.backbone = models.get_model(backbone, weights=None)
        self.fc = nn.Linear(1000, 10)
        self.loss_fn = nn.CrossEntropyLoss()

        metrics = torchmetrics.MetricCollection(
            {
                "accuracy": torchmetrics.classification.MulticlassAccuracy(num_classes=10, average="micro"),
                "precision": torchmetrics.classification.MulticlassPrecision(num_classes=10, average="micro"),
                "recall": torchmetrics.classification.MulticlassRecall(num_classes=10, average="micro"),
            }
        )
        self.train_metrics = metrics.clone(prefix="train_")
        self.val_metrics = metrics.clone(prefix="val_")
        self.test_metrics = metrics.clone(prefix="test_")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass."""
        x = nn.functional.leaky_relu(self.backbone(x))
        return self.fc(x)

    def _shared_step(self, batch):
        """Shared step for training, validation, and test steps."""
        x, y = batch
        logits = self(x)
        loss = self.loss_fn(logits, y)
        preds = torch.argmax(logits, dim=1)
        return loss, preds

    def training_step(self, batch) -> torch.Tensor:
        """Training step."""
        loss, preds = self._shared_step(batch)
        batch_metrics = self.train_metrics(preds, batch[1])
        self.log_dict(batch_metrics)
        self.log("train_loss", loss, prog_bar=True)
        return loss

    def validation_step(self, batch) -> None:
        """Validation step."""
        loss, preds = self._shared_step(batch)
        self.log("val_loss", loss, on_epoch=True)
        self.val_metrics.update(preds, batch[1])

    def on_validation_epoch_end(self) -> None:
        """Log validation metrics at the end of the epoch."""
        epoch_metrics = self.val_metrics.compute()
        self.log_dict(epoch_metrics, prog_bar=True)

    def test_step(self, batch) -> None:
        """Test step."""
        loss, preds = self._shared_step(batch)
        self.log("test_loss", loss)
        self.test_metrics.update(preds, batch[1])

    def on_test_epoch_end(self) -> None:
        """Log test metrics at the end of the epoch."""
        epoch_metrics = self.test_metrics.compute()
        self.log_dict(epoch_metrics)

    def configure_optimizers(self):
        """Configure optimizer and scheduler."""
        optimizer = torch.optim.Adam(self.parameters(), lr=1e-3)
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.1)
        return [optimizer], [scheduler]


if __name__ == "__main__":
    model = MnistClassifier()
    print(f"Model architecture: {model}")
    print(f"Number of parameters: {sum(p.numel() for p in model.parameters())}")

    dummy_input = torch.randn(1, 1, 28, 28)
    output = model(dummy_input)
    print(f"Output shape: {output.shape}")
