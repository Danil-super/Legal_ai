"""Dependency regressions for GHSA-j586-36cw-2gc2, without RSS thresholds."""

import gc
import weakref

import msgpack
import pytest
import requests
from cachecontrol.serialize import Serializer
from urllib3.response import HTTPResponse


@pytest.mark.parametrize("streaming", [False, True], ids=["unpackb", "unpacker"])
@pytest.mark.parametrize("invalid_key", [42, None], ids=["integer-key", "null-key"])
def test_rejected_map_key_releases_already_decoded_values(streaming, invalid_key):
    references = []

    class DecodedValue:
        pass

    def object_hook(value):
        decoded = DecodedValue()
        references.append(weakref.ref(decoded))
        return decoded

    payload = msgpack.packb({invalid_key: {"field": "synthetic value"}}, use_bin_type=True)
    with pytest.raises(ValueError):
        if streaming:
            unpacker = msgpack.Unpacker(object_hook=object_hook)
            unpacker.feed(payload)
            unpacker.unpack()
        else:
            msgpack.unpackb(payload, object_hook=object_hook)
    if streaming:
        del unpacker
    gc.collect()
    # The Python fallback rejects keys before decoding their values, so it may
    # produce no objects. The C extension must release objects it has constructed.
    assert all(reference() is None for reference in references)


def test_cachecontrol_serializer_preserves_response_and_rejects_invalid_map_keys():
    request = requests.Request("GET", "https://example.invalid/synthetic").prepare()
    response = HTTPResponse(body=b"synthetic\x00binary", status=200,
                            headers={"Content-Type": "application/octet-stream"},
                            preload_content=False)
    serializer = Serializer()
    serialized = serializer.dumps(request, response, body=b"synthetic\x00binary")
    restored = serializer.loads(request, serialized)
    assert restored is not None
    assert restored.status == 200
    assert restored.headers["Content-Type"] == "application/octet-stream"
    assert restored.read() == b"synthetic\x00binary"
    malformed = b"cc=4," + msgpack.packb({42: {"field": "synthetic value"}})
    assert serializer.loads(request, malformed) is None
