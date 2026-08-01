import requests

GMS_URL = "http://localhost:8080/api/graphql"

TAGS = [
    ("shadowsignal-needs-analysis", "needs-analysis", "Dataset flagged for analyst review"),
    ("shadowsignal-needs-strategy", "needs-strategy", "Dataset flagged for strategist review"),
    ("shadowsignal-needs-regcheck", "needs-regcheck", "Dataset flagged for regulatory review"),
    ("shadowsignal-needs-fix", "needs-fix", "Dataset flagged for Codeband write-back"),
    ("shadowsignal-resolved", "resolved", "Dataset gap resolved by agent crew"),
]

def create_tag(tag_id, name, description):
    query = """
    mutation CreateTag($id: String!, $name: String!, $description: String) {
      createTag(input: {id: $id, name: $name, description: $description})
    }
    """
    resp = requests.post(GMS_URL, json={
        "query": query,
        "variables": {"id": tag_id, "name": name, "description": description}
    })
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        print("FAILED " + tag_id + ": " + str(data["errors"]))
    else:
        print("OK: " + str(data["data"]["createTag"]))

if __name__ == "__main__":
    for tag_id, name, desc in TAGS:
        create_tag(tag_id, name, desc)
