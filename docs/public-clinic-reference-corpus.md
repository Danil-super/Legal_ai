# Public Dental Clinic Document Reference Corpus

## Purpose

This registry is a **development reference set**, not a legal source and not a tenant document
corpus. It records publicly accessible clinic pages that expose useful examples of contracts,
informed consents, warranty policies, patient rules, post-treatment instructions and related
materials.

The machine-readable registry is:

`services/legal_core/corpus/public_clinic_reference_sources.v1.json`

## Hard boundaries

- Do not mirror or republish third-party clinic documents into this repository without explicit
  permission or a clear licence permitting it.
- Do not cite another clinic's document as legal authority.
- Do not apply another clinic's warranty, contract or patient rules to a tenant clinic.
- Do not automatically ingest this registry into production RAG.
- Use it for taxonomy design, parser testing, document-type coverage, synthetic scenarios and
  onboarding UX design.
- Production Clinic Documents must come from the tenant clinic itself under the onboarding/data
  agreement for that clinic.

## Selected public sources

| # | Clinic | Public page | Main research value |
|---:|---|---|---|
| 1 | Клевер | https://dentklever.ru/documents/ | Contract + general/anaesthesia IDS + warranty + questionnaires |
| 2 | Дентас | https://dentas70.ru/klient/ | Broad taxonomy, specialty IDS, warranty, governance documents |
| 3 | Медлайн-Дент | https://dent.medline.pro/clinic/patcientu | Contract, warranty, patient rules, quality and privacy materials |
| 4 | Refformat | https://refformat.ru/patient/docs/ | Specialty consent library including endodontics, orthodontics, prosthodontics, sedation |
| 5 | Мидентал | https://midental.ru/company/docs/ | Contract variants, warranty and post-treatment memos |
| 6 | Адамодентал | https://adamodental-msk.ru/dokumenty/ | Contract, warranty, patient rules, implant/extraction/anaesthesia consents |
| 7 | Бел Эйра | https://beleira.ru/dokumenty/ | Contract, IDS, warranty, patient behaviour and service-result rules |
| 8 | СТОМАТОЛОГИЯ24 | https://stomatologia24.ru/dokumenty/ | Large specialty consent set and patient questionnaire |
| 9 | Saint-Dent | https://saint-dent.ru/docs/ | General and specialty consents plus questionnaire |
| 10 | Агул | https://www.agulstom.ru/dokumenty/ | Minimal intake pack: contract, IDS and personal-data consent |
| 11 | Эль-Денто / Центр стоматологии | https://centerstom.ru/yuridicheskaya-informatsiya/ | Very broad specialty IDS set, implant/sedation and warranty examples |
| 12 | Doctor Smile | https://doctorsmile.ru/clinic/dokumenty-dlya-patsientov/ | Warranty, dental anamnesis and service-term documents |
| 13 | Вилмадент | https://vilmadent.ru/about/dokumenty/ | Contract, warranty and waiting-period policy |
| 14 | SDent | https://sdent-clinic.ru/documents/ | Contract, paid-services policy, patient rules and warranty |
| 15 | Центральная стоматология | https://stomkomi.ru/patient | Contract, warranty, specialty IDS, patient memos and service/marketing consents |
| 16 | Практик-Дент | https://praktikdent.spb.ru/dokumenty | Detailed warranty and service timing material |
| 17 | Анле-Дент | https://anle-dent.ru/o_nas/polozhenie-o-garantiyah/ | Detailed warranty-policy example |
| 18 | Стоматологическая группа ГАРАНТ | https://moigarant.ru/legal-information/contract | Contract/warranty relationship and detailed guarantee clauses |
| 19 | SolidDent | https://soliddent.ru/pravo | Paid-service rules, privacy and legal-information structure |
| 20 | SSDent | https://ssdent.ru/info | Medical-record access/request templates and multiple contract variants |

## Coverage represented by the set

The registry intentionally spans more than just contracts. Important represented categories include:

- contracts for adults, minors, third-party payers and legal entities;
- general and specialty informed consents;
- therapy, surgery, prosthodontics, orthodontics, periodontics and endodontics;
- implantation, anaesthesia, sedation, radiology, whitening and hygiene;
- warranty policies and service-life conditions;
- patient rules and paid-service policies;
- medical-history/health questionnaires;
- patient memos after extraction, surgery, prosthetics and orthodontic treatment;
- medical-record access/request procedures;
- personal-data, photo/video and service-notification consents;
- examples of clinic governance documents.

## Intended next step

Build a separate `clinic-documents` tenant module with:

1. explicit clinic-owned upload;
2. immutable versions and SHA-256;
3. document classification;
4. tenant RLS isolation;
5. structured chunking that preserves clauses/sections;
6. optional embeddings within the tenant's approved privacy boundary;
7. retrieval that clearly distinguishes `LAW` evidence from `CLINIC_DOCUMENT` evidence;
8. conflict detection where a clinic document appears inconsistent with mandatory law;
9. source cards containing clinic document name, version and clause path;
10. no cross-tenant retrieval.
