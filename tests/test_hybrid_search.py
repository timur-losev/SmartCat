"""Tests for hybrid search and RRF fusion."""

from smartcat.retrieval.hybrid_search import reciprocal_rank_fusion


class TestRRF:
    def test_single_list(self):
        ranked = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7)]
        result = reciprocal_rank_fusion([ranked], k=60)
        # doc1 should be first (rank 1 → 1/(60+1))
        assert result[0][0] == "doc1"
        assert result[1][0] == "doc2"

    def test_two_lists_agreement(self):
        list1 = [("doc1", 0.9), ("doc2", 0.8)]
        list2 = [("doc1", 0.95), ("doc2", 0.7)]
        result = reciprocal_rank_fusion([list1, list2], k=60)
        # doc1 appears first in both → highest RRF score
        assert result[0][0] == "doc1"

    def test_two_lists_disagreement(self):
        list1 = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7)]
        list2 = [("doc3", 0.9), ("doc2", 0.8), ("doc1", 0.7)]
        result = reciprocal_rank_fusion([list1, list2], k=60)
        # doc2 is rank 2 in both → tied. doc1 and doc3 are rank 1 and 3 swapped.
        # doc2 gets 2 * 1/(60+2) = 2 * 0.01613 = 0.03226
        # doc1 gets 1/(60+1) + 1/(60+3) = 0.01639 + 0.01587 = 0.03226
        # doc3 gets 1/(60+3) + 1/(60+1) = same as doc1
        # All three should be very close in score
        ids = [r[0] for r in result]
        assert set(ids) == {"doc1", "doc2", "doc3"}

    def test_disjoint_lists(self):
        list1 = [("doc1", 0.9), ("doc2", 0.8)]
        list2 = [("doc3", 0.9), ("doc4", 0.8)]
        result = reciprocal_rank_fusion([list1, list2], k=60)
        # doc1 and doc3 should tie (both rank 1 in their respective lists)
        assert len(result) == 4

    def test_empty_lists(self):
        result = reciprocal_rank_fusion([], k=60)
        assert result == []

    def test_single_result(self):
        result = reciprocal_rank_fusion([[("doc1", 1.0)]], k=60)
        assert len(result) == 1
        assert result[0][0] == "doc1"

    def test_k_parameter_effect(self):
        list1 = [("doc1", 0.9), ("doc2", 0.8)]
        # With smaller k, rank differences matter more
        result_small_k = reciprocal_rank_fusion([list1], k=1)
        result_large_k = reciprocal_rank_fusion([list1], k=100)
        # Score gap should be larger with smaller k
        gap_small = result_small_k[0][1] - result_small_k[1][1]
        gap_large = result_large_k[0][1] - result_large_k[1][1]
        assert gap_small > gap_large
