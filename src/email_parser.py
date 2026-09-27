"""
Email parser for SpotThePhish.

Deterministically parses a pasted raw email or an uploaded .eml file into a structured object containing:
- sender
- Reply-To
- Return-Path
- authentication results (SPF, DKIM, DMARC)
- subject
- body text
- links (displayed text and real target)
- attachment metadata
"""