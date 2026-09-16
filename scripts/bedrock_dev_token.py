"""A short-term Amazon Bedrock key for local development — from your AWS sign-in.

Laptops never need a long-lived key: this signs a 12-hour Bedrock token with the
credentials of your current AWS CLI session (e.g. `aws sso login --profile dvv`).

    export AWS_PROFILE=dvv
    export BEDROCK_API_KEY=$(python scripts/bedrock_dev_token.py)

Dev-only dependency:  pip install aws-bedrock-token-generator
Servers use their app identity's own key instead (deploy/aws/README.md).
"""

import os
import sys

from aws_bedrock_token_generator import provide_token

print(provide_token(region=os.environ.get("BEDROCK_REGION", "ap-south-1")), end="")
sys.stderr.write("short-term Bedrock key issued (valid up to 12 hours)\n")
