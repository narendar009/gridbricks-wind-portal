"""
Convenience entry point for running data ingestion from scripts directory.
Usage:
    python backend/scripts/run_ingestion.py [--force]
"""
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.ingestion import run_ingestion

if __name__ == "__main__":
    force_download = "--force" in sys.argv
    print(f"Starting Elexon BMRS data ingestion (force={force_download})...")
    result = run_ingestion(force=force_download)
    print(f"Ingestion completed successfully: {result}")
