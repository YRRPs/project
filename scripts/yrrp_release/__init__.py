"""Stateless YRRP release preparation, launch, and receipt support."""

from .receipt import render_receipt, write_receipt

__all__ = ("render_receipt", "write_receipt")
