# Commercial-model evidence for the product improvement plan

Date checked: 2026-09-11. Status: planning evidence, not a launch decision.
Repository inspected: `68ef282ca81227546f5ceafde6ffa735ae47c7f5`.

The first objective is a product people repeatedly prefer using. Payment work
should follow evidence of that preference. The sources below establish that
one-time dictation purchases exist and identify constraints for this repository;
they do not establish Linux demand, conversion rates, or a viable selling price.

## Verified market examples

Prices are the official pages' displayed dollar amounts on the check date,
before any unverified checkout taxes, regional changes, or discounts. These are
cross-platform reference points, not directly comparable Linux offers.

| Product | Observed purchase offer | Relevant implication for investigation |
| --- | --- | --- |
| VoiceInk | Solo $25 for one Mac, Personal $39 for two, Extended $49 for three; each is a one-time purchase with advertised lifetime updates and a 14-day refund. The page shows these as discounts from $29/$49/$69. Apple Silicon and macOS 14.4+ required. | Test whether convenience and dependable local workflows justify payment; these prices do not prove what Linux users will pay. [Official pricing](https://tryvoiceink.com/pricing) |
| Superwhisper | Pro $8.49/month, $84.99/year, or $249.99 once; the same plan features, including local and cloud voice models. A license covers Mac, Windows, iPhone, and iPad. | A one-time option can coexist with recurring options. Its terms define lifetime by product/service availability and retain fair-use and termination conditions; this is not an unconditional perpetual-service promise. [Official plans](https://superwhisper.com/docs/billing/plans), [purchase terms](https://superwhisper.com/terms) |
| Wispr Flow | Free plan; Pro $15/user/month or $12/user/month with annual billing. Pro includes unlimited dictation and priority support. The page lists Mac, Windows, iOS, and Android. | Compare completion quality, vocabulary handling, and workflow convenience as well as price. Linux compatibility must be assessed separately. [Official pricing](https://wisprflow.ai/pricing) |

The Linux baseline also matters: Voxtype advertises local processing, no
subscription or telemetry, Wayland support, multiple speech engines, and an
interactive configuration interface. A paid SayItErmano must demonstrate a
specific advantage over available local alternatives. These are vendor claims,
not an independent performance assessment. [Voxtype](https://voxtype.io/)

## License facts that affect the offer

The repository's [project metadata](../../pyproject.toml) declares
`GPL-3.0-or-later`; the [README credits](../../README.md#license--credits) identify
GPLv3 prompts and sounds copied from upstream FluidVoice. That is evidence of
third-party material, not evidence that this project's maintainer owns all rights.

GPL software may be sold, including paid downloads. Recipients may redistribute
their copies, with or without a fee. For GPLv3 binaries distributed online,
equivalent access to Corresponding Source must be available at no further charge.
A paid distribution need not also be offered free to everyone, but redistribution
by recipients cannot be prohibited. [GNU licensing FAQ](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLAllowMoney)

The [repository's GPLv3 text](../../LICENSE), sections 4–6 and 10, permits paid
copies and support, requires the applicable source and notices, and bars additional
restrictions on granted rights. Section 5 also addresses modified works and
separate independent aggregates. The license text is also published by
[GNU](https://www.gnu.org/licenses/gpl-3.0.html).

Planning consequence: evaluate paid official builds, tested releases, convenient
installation, and defined support. Do not promise exclusive access to GPL code or
assume a proprietary edition can be created by changing the license file. A rights
audit must cover contributors, inherited prompts/sounds, dependencies, and model
redistribution terms before choosing any different licensing arrangement. Whether
a future extension or service is legally independent requires examination of its
actual design; a separate process alone is not a settled answer.

## Candidate models to evaluate, not implement yet

| Candidate | Proposed customer promise | What must be validated |
| --- | --- | --- |
| One-time official release purchase | Keep using the purchased local release; receive its corresponding source and a clearly bounded maintenance/support entitlement. Later major-version purchases are optional. | People value installation, updates, compatibility confidence, and support enough to pay; ongoing maintenance costs fit the price. Payment must not revoke GPL rights in delivered copies. |
| One-time supporter bundle | The usable open-source app remains available; payment buys a concrete onboarding/support package and funds development. | Buyers understand exactly what they receive; support time is capped and costed. Voluntary support alone may not support a business. |
| Local purchase plus optional cloud service | Local dictation continues independently. Separately purchased credits or a service subscription cover optional hosted processing. | There is demand for hosted features; provider cost, privacy, quotas, and service availability are transparent. Do not bundle unlimited lifetime hosted inference before measuring its cost. |

These are product/business hypotheses derived from the observations and license
constraints, not offers already validated or approved for launch. Prefer a defined
release and support promise over ambiguous lifetime wording. Do not copy a
competitor's device limits or activation restrictions into GPL distribution terms
without reviewing their compatibility with the delivered software's rights.

## Payment delivery investigation after product validation

Paddle's official developer documentation supports one-time checkout products,
subscriptions, localized prices, and payment webhooks, and describes its merchant
of record role for payments and tax. That establishes a technical candidate, not
approval for this seller or product. [Paddle developer documentation](https://developer.paddle.com/)

The later payment work should resolve seller-country/entity eligibility, actual
fees and payouts, refunds, chargebacks, tax responsibilities that remain with the
seller, and exportability of customer records. Do not select a provider from this
brief alone. A delivery design should specify receipt → verified payment event →
download/source access → purchase recovery → optional update/support eligibility,
with duplicate events and refund handling tested in a sandbox. Local dictation
should remain usable if a payment service is unavailable.

## Evidence needed before pricing work becomes a build priority

1. Observe target Linux users completing recurring real tasks; record where setup,
   transcription, editing, or insertion fails. Define a supported desktop/app
   matrix and measure successful end-to-end completion there.
2. Compare the current app against a credible local alternative on the same
   hardware and utterances. Measure corrected time to finished text, insertion
   success, latency, recovery, and setup time; avoid vendor accuracy claims.
3. Once core quality passes the plan's acceptance gates, test concrete package and
   price descriptions with users. Distinguish stated interest from actual purchase
   behavior; conducting outreach or taking payments needs its own authorized work.
4. Build conservative per-sale economics: net receipts minus expected refunds,
   distribution costs, support hours, and maintenance reserve. Treat hosted costs
   separately. Record price hypotheses only after these inputs exist.

## Method and limits

This is a bounded scan of eight official web pages plus repository metadata and
license text. Search used the available web tool; page reads also used
agent-reach's Jina route. The configured Exa route was unavailable (`Unknown MCP
server 'exa'`). GNU and Superwhisper page-read failures in the web tool were
recovered with Jina and local license text; Superwhisper's old documentation URL
redirected to the current plans page. No checkout, account creation, customer
contact, market-size estimate, performance benchmark, or legal rights audit was
performed. Recheck prices, terms, and eligibility at the later commercial decision.
