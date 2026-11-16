# SPDX-License-Identifier: Apache-2.0
"""A settings error never prints the values it read (M11b: a deploy log showed part of them)."""

import pytest
from pydantic import ValidationError

from othor.core.config import Settings


def test_a_settings_error_hides_the_values() -> None:
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, env="prod", mail_relay="postmark", secret_key="s3cr3t-value-xyz")  # type: ignore[call-arg]
    text = str(e.value)
    assert "OTHOR_POSTMARK_TOKEN" in text  # the message still says what's missing
    assert "s3cr3t-value-xyz" not in text and "input_value" not in text
