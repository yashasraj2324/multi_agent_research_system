"""
Lightweight vector store wrapper for semantic search with Chroma Cloud.
Stores embeddings with metadata and session scoping.
"""

from typing import List, Dict, Any, Optional
import chromadb
from chromadb.utils import embedding_functions

from ..core.config import config


class VectorStore:
    """Chroma Cloud Vector Search implementation with session scoping"""

    def __init__(self):
        """Initialize Chroma client and collection using config"""
        # Retrieve credentials from config
        chroma_api_key = config.CHROMA_API_KEY
        chroma_tenant = config.CHROMA_TENANT
        chroma_database = config.CHROMA_DATABASE

        # Initialize Chroma Cloud or fallback to local client if API key isn't set
        if chroma_api_key:
            self.client = chromadb.CloudClient(
                api_key=chroma_api_key,
                tenant=chroma_tenant,
                database=chroma_database
            )
        else:
            # Fallback to local persistent client
            self.client = chromadb.PersistentClient(path="./chroma_db")

        # Set up default sentence-transformer embedding function
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

        # Get or create the vector collection
        self.collection = self.client.get_or_create_collection(
            name="vector_store",
            embedding_function=self.embedding_fn
        )

    def add_chunks(self, session_id: str, chunks: List[str], metadata: Dict[str, Any]):
        """
        Add text chunks to the vector store with semantic embeddings

        Args:
            session_id: Session ID to scope this data to
            chunks: List of text chunks to store
            metadata: Common metadata for all chunks in this batch
        """
        if not chunks:
            return

        documents = []
        metadatas = []
        ids = []

        for idx, chunk in enumerate(chunks):
            # Include session_id in chunk metadata
            # Normalize metadata values to be compatible with Chroma
            doc_metadata = {}
            for key, value in metadata.items():
                # Convert list/tuple values to comma-separated strings
                if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
                    doc_metadata[key] = ",".join(map(str, value))
                else:
                    doc_metadata[key] = value

            doc_metadata["session_id"] = session_id

            documents.append(chunk)
            metadatas.append(doc_metadata)
            # Create a unique ID for each chunk insertion
            ids.append(f"{session_id}_{hash(chunk)}_{idx}")

        # Batch insert into Chroma (embeddings calculated automatically via embedding_function)
        self.collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )

    def similarity_search(self, query: str, k: int = 3, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Semantic search for top-k relevant chunks

        Args:
            query: Search query text
            k: Number of results to return
            session_id: Optional session ID to scope the search to

        Returns:
            List of most relevant document chunks with metadata
        """
        where_filter = None
        if session_id:
            where_filter = {"session_id": session_id}

        # Query Chroma collection
        results = self.collection.query(
            query_texts=[query],
            n_results=k,
            where=where_filter
        )

        formatted_results = []
        if results and results.get("documents") and results["documents"][0]:
            documents = results["documents"][0]
            metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(documents)

            for doc, meta in zip(documents, metadatas):
                formatted_results.append({
                    "text": doc,
                    "metadata": meta
                })

        return formatted_results