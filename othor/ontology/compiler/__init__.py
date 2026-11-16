# SPDX-License-Identifier: Apache-2.0
"""Ontology → spec (docs/04 §1). `compile_version` is the entry point."""

from othor.ontology.compiler.api import compile_version, spec_hash
from othor.ontology.compiler.build import COMPILER_VERSION, compile_records
from othor.ontology.compiler.render import CompileError, PersonalColumnRejected

__all__ = [
    "COMPILER_VERSION",
    "CompileError",
    "PersonalColumnRejected",
    "compile_records",
    "compile_version",
    "spec_hash",
]
