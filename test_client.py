"""Simple test script to verify the client works"""

import asyncio
from src.archives_mcp.client import ArchivesClient


async def test_search():
    """Test basic search functionality"""
    print("Testing Internet Archive search...")

    client = ArchivesClient()

    # Test search
    print("\n1. Testing search for 'Houdini'...")
    results = await client.search("Houdini", page=1, rows=5)

    if 'error' in results:
        print(f"Error: {results['error']}")
        return

    print(f"Total results: {results['total_results']}")
    print(f"Documents returned: {len(results['documents'])}")

    if results['documents']:
        doc = results['documents'][0]
        print(f"\nFirst result:")
        print(f"  Title: {doc['title']}")
        print(f"  Identifier: {doc['identifier']}")
        print(f"  URL: {doc['url']}")
        print(f"  Creators: {doc['creators']}")
        print(f"  Date: {doc.get('date', 'N/A')}")
        print(f"  Snippets: {doc['snippets'][:2]}")

        # Verify removed fields
        assert 'year' not in doc, "year should be removed from results"
        assert 'file_name' not in doc, "file_name should be removed from results"
        assert 'query' not in results, "query should be removed from results"
        print("  ✓ Verified: year, file_name, query not in results")

        # Test download
        print(f"\n2. Testing download for identifier: {doc['identifier']}")
        file_path = await client.download_text(doc['identifier'])

        if file_path is None:
            print("  Download failed (might be restricted)")
        else:
            print(f"  Downloaded to: {file_path}")
            print(f"  File exists: {file_path.exists()}")
            if file_path.exists():
                text = file_path.read_text(encoding='utf-8')
                if text == "404":
                    print("  File is cached 404 (not accessible)")
                else:
                    print(f"  File size: {len(text)} characters")
                    print(f"  First 200 chars: {text[:200]}")

    await client.close()
    print("\nTest completed successfully!")


if __name__ == "__main__":
    asyncio.run(test_search())
