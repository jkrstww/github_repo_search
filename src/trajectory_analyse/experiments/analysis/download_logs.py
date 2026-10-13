#!/usr/bin/env python

"""
Usage:

python -m analysis.download_logs evaluation/<split>/<submission>
python -m analysis.download_logs evaluation/<split>/<submission> --test  # Check files without downloading
"""

import argparse
import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

import boto3
import yaml

from botocore import UNSIGNED
from botocore.config import Config

S3_BUCKET = "swe-bench-submissions"

s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))


# s3 = boto3.client('s3')

def _list_s3_folder_content(bucket_name: str, s3_folder: str, *, verbose: bool = False) -> list[str]:
    """
    Check if files exist in a given S3 folder without downloading.
    Returns a list of file keys found.
    """
    # List the objects in the S3 folder with pagination
    paginator = s3.get_paginator("list_objects_v2")
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=s3_folder)

    files_found = []
    for page in page_iterator:
        if "Contents" in page:
            for obj in page["Contents"]:
                s3_key = obj["Key"]
                files_found.append(s3_key)
                if verbose:
                    print(f"Found: {s3_key}")

    if not files_found:
        print(f"⚠️ No files found in the S3 folder: {s3_folder}")
    else:
        print(f"Total files found: {len(files_found)}")

    return files_found


def download_s3_folder(bucket_name: str, s3_folder: str, local_folder: str) -> None:
    """
    Download all files from a given S3 folder to a local folder.
    """
    # Ensure the local folder exists
    if not os.path.exists(local_folder):
        os.makedirs(local_folder)

    # List the objects in the S3 folder with pagination
    paginator = s3.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=s3_folder)
    
    found_files = False
    for page in page_iterator:
        if 'Contents' in page:
            found_files = True
            for obj in page['Contents']:
                s3_key = obj['Key']
                local_file_path = os.path.join(local_folder, os.path.relpath(s3_key, s3_folder))

                # Create any necessary local subdirectories
                local_dir = os.path.dirname(local_file_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir)

                # Download the file from S3
                s3.download_file(bucket_name, s3_key, local_file_path)
                print(f"Downloaded {s3_key} to {local_file_path}")
    
    if not found_files:
        print(f"⚠️ No files found in the S3 folder: {s3_folder}")


def _submission_metadata(submission_path: str) -> dict:
    """Load a submission's metadata, if present."""
    for name in ("metadata.yaml", "metadata.yml"):
        path = os.path.join("evaluation", submission_path, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def _submission_repo(metadata: dict) -> str | None:
    """The submission's own artifact repo, from metadata's `assets.repo`.

    Submissions made from mid-2026 on host their own logs and trajectories in a public
    GitHub repo rather than in our S3 bucket; older entries have no `repo` and are
    still served from S3.
    """
    return (metadata.get("assets") or {}).get("repo")


def _artifact_s3_location(
    submission_path: str, folder: str, metadata: dict
) -> tuple[str, str] | None:
    """Resolve an artifact folder to an S3 bucket and key prefix.

    An explicit null means the artifact is unavailable. Missing or non-S3 asset
    values retain the legacy bucket/path behavior.
    """
    assets = metadata.get("assets") or {}
    if folder in assets and assets[folder] is None:
        return None

    asset_url = assets.get(folder)
    if isinstance(asset_url, str):
        parsed = urlparse(asset_url)
        if parsed.scheme == "s3" and parsed.netloc and parsed.path.strip("/"):
            return parsed.netloc, parsed.path.strip("/")

    return S3_BUCKET, f"{submission_path}/{folder}"


def download_from_repo(repo_url: str, submission_path: str, folders: list[str]) -> None:
    """Clone a self-hosted submission repo and copy its artifact folders into place."""
    with tempfile.TemporaryDirectory() as tmp:
        clone = os.path.join(tmp, "submission")
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, clone],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"⚠️ Could not clone {repo_url}: {proc.stderr.strip()}")
            return
        for folder in folders:
            src = os.path.join(clone, folder)
            if not os.path.isdir(src):
                print(f"⚠️ {repo_url} has no {folder}/")
                continue
            dest = os.path.join("evaluation", submission_path, folder)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            print(f"Downloaded {folder} from {repo_url} to {dest}")


def _check_submission(submission_path: str, folders: list[str]) -> None:
    """Check the configured artifact locations for one submission."""
    metadata = _submission_metadata(submission_path)
    print(f"\nChecking submission: {submission_path}")
    for folder in folders:
        print(f"Checking folder: {folder}")
        location = _artifact_s3_location(submission_path, folder, metadata)
        if location is None:
            print(f"Artifact unavailable: metadata sets assets.{folder} to null")
            continue
        bucket_name, s3_folder = location
        _list_s3_folder_content(bucket_name, s3_folder)


def _check_submissions(submission_path: str, folders: list[str]) -> None:
    """
    Check if files exist in S3 for given submission(s) without downloading
    """
    if any(submission_path.removesuffix("/").endswith(x) for x in ["lite", "test", "verified", "bash-only"]):
        # Get all the submissions under a given split
        submission_paths = sorted(
            [os.path.join(submission_path, x) for x in os.listdir(f"evaluation/{submission_path}")]
        )
        for sub_path in submission_paths:
            _check_submission(sub_path, folders)
    else:
        _check_submission(submission_path, folders)


def download_submission(submission_path: str, use_cli: bool, folders: list[str]) -> None:
    """
    Download logs from S3 bucket for a given submission
    """
    # Check that submission path exists locally
    if not os.path.exists(os.path.join("evaluation", submission_path)):
        raise ValueError(f"Submission '{submission_path}' does not exist (should exist under `evaluation/`)")

    metadata = _submission_metadata(submission_path)
    repo_url = _submission_repo(metadata)
    if repo_url:
        return download_from_repo(repo_url, submission_path, folders)

    for folder in folders:
        location = _artifact_s3_location(submission_path, folder, metadata)
        if location is None:
            print(f"Artifact unavailable: metadata sets assets.{folder} to null")
            continue
        bucket_name, s3_path = location
        local_folder = os.path.join("evaluation", submission_path, folder)
        if use_cli:
            # Download the folder using the AWS CLI
            if not os.path.exists(local_folder):
                os.makedirs(local_folder)
            subprocess.run(
                ["aws", "s3", "cp", f"s3://{bucket_name}/{s3_path}", local_folder, "--recursive"],
                check=True,
            )
        else:
            # Download the folder using the boto3 client
            download_s3_folder(bucket_name, s3_path, local_folder)


def main(
    submission_path: str, skip_existing: bool, use_cli: bool, only_logs: bool, only_trajs: bool, test: bool
) -> None:
    submission_path = submission_path.removesuffix("/")
    # Remove 'evaluation/' prefix if present
    if submission_path.startswith("evaluation/"):
        submission_path = submission_path[len("evaluation/"):]

    folders = ["logs", "trajs"]
    if only_logs:
        folders = ["logs"]
    elif only_trajs:
        folders = ["trajs"]

    if test:
        return _check_submissions(submission_path, folders)

    if any(submission_path.endswith(x) for x in ["lite", "test", "verified", "bash-only"]):
        # Get all the submissions under a given split
        submission_paths = sorted([
            os.path.join(submission_path, x)
            for x in os.listdir(f"evaluation/{submission_path}")
        ])
        for submission_path in submission_paths:
            if skip_existing and all([
                os.path.exists(os.path.join("evaluation", submission_path, folder))
                and len(os.listdir(os.path.join("evaluation", submission_path, folder))) > 0
                for folder in folders
            ]):
                # Skip if flag specified, folder(s) exist, and folder(s) are not empty
                print(f"Skipping {submission_path} (already downloaded)")
                continue
            download_submission(submission_path, use_cli, folders)
    else:
        download_submission(submission_path, use_cli, folders)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_path", type=str, help="Path to the evaluation folder")
    parser.add_argument("--use_cli", action="store_true", help="Use AWS credentials to download logs")
    parser.add_argument("--skip_existing", action="store_true", help="Skip downloading if the folder already exists")
    parser.add_argument("--only_logs", action="store_true", help="Only download logs")
    parser.add_argument("--only_trajs", action="store_true", help="Only download trajs")
    parser.add_argument("--test", action="store_true", help="Check if files exist without downloading")
    main(**vars(parser.parse_args()))
