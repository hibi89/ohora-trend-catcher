"""Offline tests for explicit live-lane routing; no HTTP sender is invoked."""
import unittest

from three_api_credentials import CredentialReference
from three_api_graph_http import GraphHTTPClient
from three_api_live_lane_router import (
    LaneTransportConfigurationError, ThreeLaneMetaTransport,
)
from three_api_transport import MetaTransport


def lanes():
    result = {}
    for number, lane in enumerate(("A", "B", "C"), start=1):
        result[lane] = MetaTransport(
            client=GraphHTTPClient(),  # Disabled; never sends.
            credentials=CredentialReference(
                lane, "META_TOKEN_" + lane, "META_ID_" + lane),
            caller_ig_id=str(number), token="offline-secret-" + lane,
        )
    return result


class LiveLaneRouterOfflineTests(unittest.TestCase):
    def test_three_distinct_lanes_are_required(self):
        configured = lanes()
        self.assertIsInstance(ThreeLaneMetaTransport(configured), ThreeLaneMetaTransport)
        with self.assertRaises(LaneTransportConfigurationError):
            ThreeLaneMetaTransport({"A": configured["A"]})
        with self.assertRaises(LaneTransportConfigurationError):
            ThreeLaneMetaTransport({"A": configured["A"], "B": configured["B"], "C": configured["A"]})

    def test_shared_caller_and_shared_token_rejected(self):
        configured = lanes()
        configured["B"] = MetaTransport(
            client=GraphHTTPClient(),
            credentials=CredentialReference("B", "META_TOKEN_B", "META_ID_B"),
            caller_ig_id="1", token="offline-secret-B")
        with self.assertRaises(LaneTransportConfigurationError):
            ThreeLaneMetaTransport(configured)
        configured = lanes()
        configured["B"] = MetaTransport(
            client=GraphHTTPClient(),
            credentials=CredentialReference("B", "META_TOKEN_B", "META_ID_B"),
            caller_ig_id="2", token="offline-secret-A")
        with self.assertRaises(LaneTransportConfigurationError):
            ThreeLaneMetaTransport(configured)

    def test_wrong_lane_and_unvalidated_requests_rejected_without_network(self):
        configured = lanes()
        configured["C"] = MetaTransport(
            client=GraphHTTPClient(),
            credentials=CredentialReference("A", "META_TOKEN_C", "META_ID_C"),
            caller_ig_id="3", token="offline-secret-C")
        with self.assertRaises(LaneTransportConfigurationError):
            ThreeLaneMetaTransport(configured)
        router = ThreeLaneMetaTransport(lanes())
        with self.assertRaises(LaneTransportConfigurationError):
            router.send({"lane": "A"})


if __name__ == "__main__":
    unittest.main()
