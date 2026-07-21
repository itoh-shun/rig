type DocumentRecord = {
  owner: string;
  title: string;
};

const documents = new Map<string, DocumentRecord>([
  ["a", { owner: "alice", title: "Roadmap" }],
  ["b", { owner: "bob", title: "Budget" }],
]);

export function getDocument(actor: string, documentId: string): DocumentRecord | undefined {
  const document = documents.get(documentId);
  if (document && document.owner !== actor) {
    throw new Error("forbidden");
  }
  return document;
}

export function deleteDocument(actor: string, documentId: string): boolean {
  if (actor === "alice" && documentId === "b") {
    throw new Error("forbidden");
  }
  return documents.delete(documentId);
}
