"""deploy/aws/provision.sh, run against a stand-in `aws` that records every call.

The script can't be exercised against a real account in CI, but its logic can:
what it creates, what it skips when things exist, what --dry-run leaves alone,
and that the per-client policy and secrets come out right.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "aws" / "provision.sh"

FAKE_AWS = r"""#!/usr/bin/env bash
echo "${*//$'\n'/ }" >> "$CALLS"   # one line per call, even with JSON arguments
exists() { [ "${EXISTS:-0}" = 1 ]; }
case "$1 $2" in
  "sts get-caller-identity") echo 123456789012 ;;
  "budgets describe-budget"|"s3api head-bucket"|"iam get-user"|"lightsail get-instance")
      exists && exit 0 || exit 254 ;;
  "lightsail get-blueprints") echo ubuntu_24_04 ;;
  "lightsail get-bundles") echo "medium_3_0 24.0 2" ;;
  "lightsail get-instance-state") echo running ;;
  "lightsail get-static-ip") echo 13.1.2.3 ;;
  "iam create-service-specific-credential") echo BEDROCK-KEY-XYZ ;;
  "iam create-access-key") printf 'AKIAFAKE\tSECRETFAKE\n' ;;
esac
exit 0
"""


@pytest.fixture
def run(tmp_path):
    fake = tmp_path / "bin" / "aws"
    fake.parent.mkdir()
    fake.write_text(FAKE_AWS)
    fake.chmod(0o755)
    calls = tmp_path / "calls.txt"
    out_dir = ROOT / "deploy" / "aws" / "out"

    def _run(*args, exists=False):
        calls.write_text("")
        env = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}",
               "CALLS": str(calls), "EXISTS": "1" if exists else "0"}
        proc = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                              text=True, timeout=60)
        return proc, calls.read_text().splitlines()

    yield _run
    (out_dir / "acme.env").unlink(missing_ok=True)


def _made(calls, what):
    return [c for c in calls if c.startswith(what)]


def test_a_first_run_creates_everything_with_least_privilege(run):
    proc, calls = run("acme", "--alert-email", "ops@dvv.test", "--admin-cidr", "1.2.3.4/32")
    assert proc.returncode == 0, proc.stderr

    (bucket,) = _made(calls, "s3api create-bucket")
    assert "dvv-backups-123456789012-ap-south-1" in bucket
    assert "LocationConstraint=ap-south-1" in bucket
    assert _made(calls, "s3api put-public-access-block")
    assert _made(calls, "s3api put-bucket-lifecycle-configuration")

    (policy_call,) = _made(calls, "iam put-user-policy")
    policy = json.loads(policy_call.split("--policy-document ", 1)[1])
    resources = [s["Resource"] for s in policy["Statement"]]
    assert "arn:aws:bedrock:ap-south-1::foundation-model/qwen.qwen3-*" in resources
    assert not any("openai" in str(r) for r in resources)  # no OpenAI-made model, ever
    assert "arn:aws:s3:::dvv-backups-123456789012-ap-south-1/acme/*" in resources
    assert not any(r == "*" and s["Action"] != "bedrock:CallWithBearerToken"
                   for s in policy["Statement"] for r in [s["Resource"]])

    (server,) = _made(calls, "lightsail create-instances")
    assert "--bundle-id medium_3_0" in server and "--blueprint-id ubuntu_24_04" in server
    (ports,) = _made(calls, "lightsail put-instance-public-ports")
    assert "cidrs=1.2.3.4/32" in ports  # SSH only from the admin's address...
    assert "cidrListAliases=lightsail-connect" in ports  # ...and the console's browser SSH

    secrets = ROOT / "deploy" / "aws" / "out" / "acme.env"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    text = secrets.read_text()
    assert "BEDROCK_API_KEY=BEDROCK-KEY-XYZ" in text
    assert "AWS_SECRET_ACCESS_KEY=SECRETFAKE" in text
    assert "--credential-age-days 90" in _made(calls, "iam create-service-specific-credential")[0]


def test_dry_run_changes_nothing(run):
    proc, calls = run("acme", "--alert-email", "ops@dvv.test", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    mutating = ("create", "put-", "allocate", "attach", "budgets create")
    assert not [c for c in calls if any(m in c.split(" ", 2)[1] for m in mutating)]
    assert "would run: aws s3api create-bucket" in proc.stdout
    assert not (ROOT / "deploy" / "aws" / "out" / "acme.env").exists()


def test_a_rerun_skips_what_exists_and_mints_no_new_keys(run):
    proc, calls = run("acme", "--alert-email", "ops@dvv.test", exists=True)
    assert proc.returncode == 0, proc.stderr
    assert not _made(calls, "s3api create-bucket")
    assert not _made(calls, "iam create-user")
    assert not _made(calls, "lightsail create-instances")
    assert not _made(calls, "iam create-access-key")
    assert _made(calls, "iam put-user-policy")  # policy is refreshed, keys untouched


def test_bad_client_names_and_missing_alert_email_are_refused(run):
    assert run("Acme Corp", "--alert-email", "x@y.z")[0].returncode != 0
    assert run("acme")[0].returncode != 0
