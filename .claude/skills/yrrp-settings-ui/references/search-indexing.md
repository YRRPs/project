# Search and indexing mechanics

Read this file completely before changing YRRPs navigation, search metadata, page titles, or homepage placement. Paths and line ranges below describe the builder checkout inspected on 2026-10-07.

## Page eligibility

Every searchable YRRPs page needs all of these:

- a non-empty, `yrrp_`-prefixed root `PreferenceScreen` key;
- `@SearchIndexable` and a `SEARCH_INDEX_DATA_PROVIDER` for its XML/page model;
- its fragment in `SettingsGateway.ENTRY_FRAGMENTS`;
- the same string resource on the navigation row and target page root.

Current provider precedent is in `packages/apps/Settings/src/com/android/settings/yrrp/YrrpSettings.java:37-49`, `YrrpPulseSettings.java:51-63`, and `YrrpScreenOffAnimationSettings.java:37-49`. Gateway registration is in `packages/apps/Settings/src/com/android/settings/core/gateway/SettingsGateway.java:438-440`.

A root key is not cosmetic. Settings Intelligence adds an index row only when its key is non-empty; otherwise it logs and drops it (`packages/apps/SettingsIntelligence/src/com/android/settings/intelligence/search/indexing/IndexDataConverter.java:388-393`).

## Per-element searchability

`settings:searchable` is read from each parsed preference element, not inherited from a parent. `PreferenceXmlParserUtils` iterates every supported preference and stores that element's searchable attribute (`packages/apps/Settings/src/com/android/settings/core/PreferenceXmlParserUtils.java:127-180`). Therefore every `PreferenceCategory`, `TopIntroPreference`, and `FooterPreference` must set `settings:searchable="false"` itself.

Put useful synonyms in `settings:keywords` on the hub entry and primary control. Do not put keywords on structural rows. Search SQL returns only rows whose indexed `enabled` column is `1` (`packages/apps/SettingsIntelligence/src/com/android/settings/intelligence/search/query/DatabaseResultTask.java:235-267`), so controller availability and non-indexable-key behavior remain part of search debugging.

## Navigation identity and breadcrumbs

Use one title resource for a hub row and the target root. This keeps the visible title, site-map node, and breadcrumb identity aligned. A hub-row result and a page-header result may both appear; this duplication is accepted when both open the correct page with the correct parent.

When a result is absent, check in this order: root key, provider, gateway, per-element searchability, title/fragment target, non-indexable keys/controller availability, then index freshness. Do not treat a stale device index as source failure until those are separated.

## Homepage highlighting

Each homepage XML variant is independent. A highlight key may appear once in standard and once in expressive XML, but must be unique inside either file. `HighlightableMenu.fromXml()` stores `menuKey → preferenceKey` in a map, so a later duplicate overwrites the earlier tile (`packages/apps/Settings/src/com/android/settings/homepage/HighlightableMenu.java:64-92`). Both variants must contain the YRRPs tile.

## Reindex behavior

Settings Intelligence records the current locale and `Build.VERSION.INCREMENTAL`; a locale change or OTA causes a full index (`packages/apps/SettingsIntelligence/src/com/android/settings/intelligence/search/indexing/IndexDatabaseHelper.java:295-336`). Full rebuild drops old-language and deprecated rows (`packages/apps/SettingsIntelligence/src/com/android/settings/intelligence/search/indexing/DatabaseIndexingManager.java:133-148`). During development, a new build fingerprint, locale change, or clearing Settings Intelligence data may be needed before new entries appear. Record which trigger was used; do not claim an XML change was ineffective based only on a stale index.

## Required evidence

Quote the structural checker output, then verify representative exact titles, synonyms, breadcrumb parent, destination, and homepage highlighting on device. The checker cannot prove index freshness or routing behavior.
