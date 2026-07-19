#!/bin/bash
PROJECT_NAME="rosora"

# This script prepares an image for use with the rosora tool.

# Usage: Run from the file's directory: ./prepare_image.sh <remote_destination>

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <remote_destination>"
    exit 1
fi

REMOTE_DESTINATION=$1

# Get the Repository Root
REPO_ROOT=$(git rev-parse --show-toplevel)
echo "Copying files from: $REPO_ROOT to $REMOTE_DESTINATION:/usr/local/$PROJECT_NAME"

# RSYNC the relevant files to the remote destination
rsync -av --exclude=CHANGELOG.md  \
    --exclude=README.md \
    --exclude=conf \
    --exclude=bumpversion.cfg \
    --exclude=pyproject.toml \
    --exclude=tools \
    --exclude=utils/prepare_image.sh \
    --exclude=.git \
    --exclude=.idea \
    "$REPO_ROOT/" "$REMOTE_DESTINATION:/usr/local/$PROJECT_NAME/"
