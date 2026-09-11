# Recording consent — SayItErmano real-speech corpus

- Status: **TEMPLATE — read aloud, adapt wording locally, collect a
  signed/dated copy per speaker**
- Source: [corpus-spec.md §1](corpus-spec.md) (the numbered substance is
  pinned there; keep all five numbered points when adapting wording)
- Applies to: every recording session for the evaluation corpus
  (docs/eval/corpus-spec.md)

How to use this template:

1. The moderator reads the brief aloud (or hands it over) **before any
   recording starts**, in the speaker's preferred language version
   below.
2. The speaker fills in the fill-in fields and signs (paper, or a
   signed text file / typed name + date + emailed confirmation).
3. Signed forms live **outside git**, in
   `~/.local/share/sayit-ermano-eval/consent/<speaker-id>-<date>.pdf`
   (or `.txt`). Never commit a signed form or any contact data.
4. Record the reference in the batch manifest's `speakers[].consent_ref`
   (e.g. `S07 consent 2026-09-20, signed form on file`), so the
   validator can check consent presence per speaker.
5. Withdrawal (point 4) is served by deleting everything keyed to the
   speaker id: audio, masters, transcripts, manifest rows — the id
   (`S07`) is deliberately embedded in every file name and case id so
   the deletion is mechanical.

Fill-in fields: `<MODERATOR-CONTACT>` (name + contact of the person
responsible for the corpus), `<PROJECT-URL>` (this repository),
`<SPEAKER-ID>` (assigned pseudonym, e.g. `S07`), `<DATE>`.

---

## English

**Recording consent — SayItErmano speech corpus**

Moderator: <MODERATOR-CONTACT> · Project: <PROJECT-URL> · Speaker ID:
<SPEAKER-ID> · Date: <DATE>

You are recording short spoken utterances to help evaluate and improve
SayItErmano, a local speech-dictation application.

1. We record your voice saying everyday things: short commands,
   messages, names, numbers, and technical phrases from prompts we give
   you. Please avoid personal information you don't want in a
   transcript.
2. Recordings and transcripts are stored locally on the project's test
   machine, are **not published or shared**, and are not used to train
   third-party or cloud services.
3. We may publish aggregate, anonymous results (error rates, timings).
   We may later ask you separately to license specific recordings for a
   public corpus; nothing is published without that separate consent.
4. You can withdraw at any time; we delete your recordings and
   transcripts (speaker ID <SPEAKER-ID> lets us find them) on request.
5. Participation is unpaid (thank-you credit in private notes only).
   The session takes about 45–60 minutes.

Name: ____________________ Date: ____________ Signature: ____________________

---

## Српски (Srpski)

**Saglasnost za snimanje — govorni korpus za SayItErmano**

Moderator: <MODERATOR-CONTACT> · Projekat: <PROJECT-URL> · ID govornika:
<SPEAKER-ID> · Datum: <DATE>

Snimate kratke izgovorene replike kako biste pomogli evaluaciji i
poboljšanju SayItErmano, lokalne aplikacije za diktiranje govorom.

1. Snimamo vaš glas kako izgovarate svakodnevne stvari: kratke
   komande, poruke, imena, brojeve i tehničke fraze iz promptova koje
   dobijate od nas. Molimo izbegavate lične informacije koje ne želite
   u transkriptu.
2. Snimci i transkripti čuvaju se lokalno na testnoj mašini projekta,
   **ne objavljuju se niti dele**, i ne koriste se za obuku trećih ili
   cloud servisa.
3. Možemo objaviti agregirane, anonimne rezultate (stope grešaka,
   vremenske mere). Kasnije vas možemo posebno zamoliti da licencirate
   pojedinačne snimke za javni korpus; ništa se ne objavljuje bez te
   posebne saglasnosti.
4. Saglasnost možete povući u svakom trenutku; na zahtev brišemo vaše
   snimke i transkripte (ID govornika <SPEAKER-ID> nam omogućava da ih
   pronađemo).
5. Učešće je neplaćeno (zahvalnica u privatnim beleškama). Sesija traje
   oko 45–60 minuta.

Ime: ____________________ Datum: ____________ Potpis: ____________________
