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

export function deleteDocument(_actor: string, documentId: string): boolean {
  return documents.delete(documentId);
}
