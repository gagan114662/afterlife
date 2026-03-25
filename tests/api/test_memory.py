"""Tests for the memory module embeddings."""
from unittest.mock import MagicMock, patch


def test_embeddings_not_zero():
    """Verify that get_embedding returns real content, not all zeros."""
    from services.api.memory import get_embedding

    mock_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(embedding=mock_embedding)]

    with patch("services.api.memory._get_anthropic_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_client_fn.return_value = mock_client

        result = get_embedding("hello world")

    assert result == mock_embedding
    assert result != [0.0] * len(result), "Embeddings must not be all zeros"
    assert len(result) > 0


def test_embeddings_called_with_correct_model():
    """Verify that the embedding API is called with the voyage-3 model."""
    from services.api.memory import get_embedding

    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(embedding=[0.1])]

    with patch("services.api.memory._get_anthropic_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_client_fn.return_value = mock_client

        get_embedding("test text")

        mock_client.embeddings.create.assert_called_once_with(
            model="voyage-3",
            input=["test text"],
        )
