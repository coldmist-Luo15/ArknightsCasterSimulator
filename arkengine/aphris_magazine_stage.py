"""Public stage entry for Aphris."""
from .aphris_stage import run_aphris_stage


def run_aphris_magazine_stage(tables,request):
    return run_aphris_stage(tables,request)
