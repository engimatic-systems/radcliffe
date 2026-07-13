from radcliffe_media.worker import info


def test_worker_info() -> None:
    assert info().name == "radcliffe-media-worker"
