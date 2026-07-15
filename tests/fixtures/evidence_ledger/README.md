# Evidence Ledger fixture

This small, sanitized corpus contains one XHS note and one Douyin video about
AI coding.  It is intentionally tiny but retains URLs, identifiers, authors,
publication/crawl timestamps, interaction fields, and platform-specific
fields.  Future relevance filtering, cross-platform de-duplication, Claim
generation, and report evaluation can reuse it without relying on live data.

The two records describe a similar topic but have different platform IDs, so
they must remain separate evidence items.  `platform_marker` is deliberately
platform-specific and must survive in an EvidenceItem's `metadata.raw`.
