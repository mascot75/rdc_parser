# -*- coding: utf-8 -*-
"""Compatibility entrypoint for the synchronous replay exporter."""
import os
import runpy
if __name__ == "__main__":
    runpy.run_path(os.path.join(os.path.dirname(__file__), "replay_export_ui.py"), run_name="__main__")
