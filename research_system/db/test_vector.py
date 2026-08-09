#!/usr/bin/env python3
"""
Test script for vector.py functionality
"""

import os
import sys
sys.path.insert(0, '.')

def test_vector_store():
    print("Testing VectorStore...")

    # Import and initialize
    from research_system.db.vector import VectorStore
    vector_store = VectorStore()

    # Test data
    session_id = "test_session_001"
    chunks = [
        "The quick brown fox jumps over the lazy dog.",
        "Machine learning is a subset of artificial intelligence.",
        "Deep learning uses neural networks with multiple layers.",
        "Natural language processing enables computers to understand human language.",
        "Vector databases store high-dimensional vectors for similarity search."
    ]

    metadata = {
        "source": "test_document",
        "author": "test_author",
        "topic": "machine learning basics"
    }

    # Add chunks
    print(f"Adding {len(chunks)} chunks to session {session_id}")
    vector_store.add_chunks(session_id, chunks, metadata)
    print("Chunks added successfully!")

    # Test similarity search
    query = "What is machine learning?"
    print(f"\nSearching for: '{query}'")
    results = vector_store.similarity_search(query, k=3)

    print(f"Found {len(results)} results:")
    for i, result in enumerate(results, 1):
        print(f"{i}. {result['text']}")
        print(f"   Metadata: {result['metadata']}")
        print()

    # Test session-scoped search
    print(f"Searching within session {session_id} for: 'neural networks'")
    session_results = vector_store.similarity_search("neural networks", k=2, session_id=session_id)
    print(f"Found {len(session_results)} session-scoped results:")
    for i, result in enumerate(session_results, 1):
        print(f"{i}. {result['text']}")
        print(f"   Session ID: {result['metadata'].get('session_id')}")
        print()

    print("VectorStore test completed successfully!")

if __name__ == "__main__":
    test_vector_store()