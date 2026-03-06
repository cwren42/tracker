# SOC2 Upload Runbook

**Date:** 2026-03-02  
**Prepared By:** GitHub Copilot

## Ready Files
- Full queue: `SOC2-Upload-Queue.csv` (58 evidence files)
- First upload batch: `SOC2-Upload-Batch-01.csv` (12 files)
- Staging folder for first batch: `../10-Archive/Upload-Staging/Batch-01`

## Upload Sequence (recommended)
1. Upload governance + policy artifacts first (EC-001 to EC-012).
2. Map each upload to its corresponding control row (`SG-###`) from `SOC2-Control-Implementation-Matrix.md`.
3. Use `EC-###` in the StrikeGraph evidence title/description.
4. Mark completion in your internal tracker after each successful upload.

## Batch 01 Checklist
- [ ] EC-001
- [ ] EC-002
- [ ] EC-003
- [ ] EC-004
- [ ] EC-005
- [ ] EC-006
- [ ] EC-007
- [ ] EC-008
- [ ] EC-009
- [ ] EC-010
- [ ] EC-011
- [ ] EC-012

## Notes
- All 58 `EC-###` files already exist in StrikeGraph folders and are upload-ready templates.
- If you want, next step is auto-creating Batch-02 through Batch-05 and staging each folder the same way.
