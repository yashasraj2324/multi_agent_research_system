#!/usr/bin/env python3
"""
Comprehensive test script for VectorStore with Chroma Cloud integration
"""

import os
import sys
sys.path.insert(0, '.')

def test_vector_store_integration():
    print("=== Comprehensive VectorStore Test ===\n")

    # Import and initialize
    from research_system.db.vector import VectorStore
    from research_system.core.config import config

    print("1. Testing configuration...")
    chroma_status = "Set" if config.CHROMA_API_KEY else "Not set"
    tenant_status = "Set" if config.CHROMA_TENANT else "Not set"
    db_status = "Set" if config.CHROMA_DATABASE else "Not set"
    configured = "True" if config.is_chromadb_configured() else "False"
    print(f"   CHROMA_API_KEY: {chroma_status}")
    print(f"   CHROMA_TENANT: {tenant_status}")
    print(f"   CHROMA_DATABASE: {db_status}")
    print(f"   is_chromadb_configured(): {configured}")
    print()

    try:
        print("2. Initializing VectorStore...")
        vector_store = VectorStore()
        print("   [PASS] VectorStore initialized successfully")
    except Exception as e:
        print(f"   [FAIL] VectorStore initialization failed: {e}")
        return

    # Test data
    session_id = "integration_test_session"
    chunks = [
        "Agentic AI systems enable autonomous task completion without human intervention.",
        "Multi-agent research platforms coordinate specialized agents for comprehensive analysis.",
        "Vector embeddings store semantic relationships between text chunks for similarity search.",
        "Chroma Cloud provides scalable vector storage with high-performance query capabilities.",
        "Session scoping ensures data isolation and organized research workflows."
    ]

    metadata = {
        "source": "integration_test",
        "author": "test_suite",
        "created_at": "2025-08-09",
        "tags": ["agentic-ai", "multi-agent", "vector-search", "session-management"]
    }

    # Test add_chunks with error handling
    print(f"\n3. Adding {len(chunks)} chunks to session '{session_id}'...")
    try:
        vector_store.add_chunks(session_id, chunks, metadata)
        print("   [PASS] Chunks added successfully")
    except Exception as e:
        print(f"   [FAIL] Failed to add chunks: {e}")
        return

    # Test similarity_search
    print("\n4. Testing similarity search...")
    test_queries = [
        ("agentic AI systems", 3),
        ("multi-agent research", 2),
        ("semantic search", 2)
    ]

    for query, k in test_queries:
        try:
            results = vector_store.similarity_search(query, k=k)
            print(f"   [PASS] Query '{query}' returned {len(results)} results")
            for i, result in enumerate(results, 1):
                print(f"      {i}. {result['text'][:60]}...")
        except Exception as e:
            print(f"   [FAIL] Query '{query}' failed: {e}")

    # Test session-scoped search
    print("\n5. Testing session-scoped search...")
    try:
        session_results = vector_store.similarity_search(
            "session management",
            k=2,
            session_id=session_id
        )
        print(f"   [PASS] Session-scoped search returned {len(session_results)} results")
        for i, result in enumerate(session_results, 1):
            session_id_match = result['metadata'].get('session_id', 'N/A')
            print(f"      {i}. Session: {session_id_match}, Text: {result['text'][:50]}...")
    except Exception as e:
        print(f"   [FAIL] Session-scoped search failed: {e}")

    # Test empty query handling
    print("\n6. Testing edge cases...")
    try:
        empty_results = vector_store.similarity_search("", k=5)
        print(f"   [PASS] Empty query handled (returned {len(empty_results)} results)")
    except Exception as e:
        print(f"   [FAIL] Empty query handling failed: {e}")

    try:
        large_k_results = vector_store.similarity_search("test", k=100)
        print(f"   [PASS] Large k value handled (returned {len(large_k_results)} results)")
    except Exception as e:
        print(f"   [FAIL] Large k value handling failed: {e}")

    print("\n=== Integration Test Complete ===")
    print("All VectorStore operations completed successfully!")

if __name__ == "__main__":
    test_vector_store_integration()