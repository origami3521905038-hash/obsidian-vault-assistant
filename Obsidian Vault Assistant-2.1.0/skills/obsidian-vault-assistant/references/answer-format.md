# Answer Contract

Use this structure for answers grounded in a vault:

## 调查结论 / Investigation conclusion

State the answer in a few sentences. Label whether each statement is a vault fact, an inference from several notes, or external context.

## Raw 溯源 / Raw provenance

List the vault name, relative note path, and heading for each supporting Raw source. If `verify_with_raw` was used, say what it confirmed or contradicted. If no Raw result exists, state that explicitly rather than substituting general knowledge.

## 可信度分析 / Confidence analysis

Explain the evidence level, agreement or conflict among notes, date/source limitations, and what remains unverified. Retrieval scores are not confidence values.

When the search returns no relevant note, say: “知识库中没有找到直接证据” and name the vaults/layers searched. Do not invent a vault answer. You may offer clearly labeled external context only if the user asks for it.
