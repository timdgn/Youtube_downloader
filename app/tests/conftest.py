"""Shared fixtures: one fake per adapter boundary."""

from __future__ import annotations

import pytest

from tests.fakes.dynamo_fake import FakeDynamoRepository
from tests.fakes.extractor_fake import FakeExtractor
from tests.fakes.s3_fake import FakeMediaStore
from tests.fakes.sqs_fake import FakeJobQueue
from tests.fakes.ssm_fake import FakeSsmClient


@pytest.fixture
def dynamo() -> FakeDynamoRepository:
    return FakeDynamoRepository()


@pytest.fixture
def media() -> FakeMediaStore:
    return FakeMediaStore()


@pytest.fixture
def queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def ssm() -> FakeSsmClient:
    return FakeSsmClient()


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()
