# SmartCat Evaluation Questions

50 вопросов для оценки качества RAG. Сравнение: до и после QA-extraction.

Метрики:
- **Relevance** (0-3): насколько ответ релевантен вопросу
- **Accuracy** (0-3): фактическая точность (можно проверить по email)
- **Citations** (0/1): есть ли ссылки на конкретные письма
- **Latency** (сек): время до финального ответа

---

## Category 1: Factual Lookup (точный поиск факта)

1. When did Enron file for Chapter 11 bankruptcy?
2. Who sent the email about "Pre-Bankruptcy Bonuses" and what amount was mentioned?
3. What was the total amount of retention bonuses paid before Enron's bankruptcy?
4. Who is Jeff Dasovich and what was his role based on his emails?
5. What is the Schedule Crawler and why did HourAhead failures occur?
6. When did PG&E file for Chapter 11?
7. Who sent emails about "Demand Ken Lay Donate Proceeds from Enron Stock Sales"?
8. What was the subject of Ken Lay's email on November 30, 2001?
9. How many emails did Vince Kaminski send and what topics did he cover?
10. What energy units (MMBtu, MWh) appear most frequently in the corpus?

## Category 2: People & Relationships (кто с кем общался)

11. Who were the most frequent email senders at Enron?
12. Which people communicated most with Jeff Skilling?
13. Who was Sara Shackleton and what department did she work in?
14. What was Tana Jones responsible for based on her email patterns?
15. Who were the key people involved in California energy discussions?
16. Find emails between Kay Mann and external law firms.
17. Who reported to Sally Beck based on email patterns?
18. What was Chris Germany's area of responsibility?
19. Which external parties (non-Enron) appear most in the correspondence?
20. Find communications between Enron and government regulators.

## Category 3: Events & Timeline (хронология событий)

21. What happened at Enron in October 2001?
22. What were the key events in the California energy crisis as discussed in emails?
23. When did employees first start discussing potential bankruptcy?
24. What was the timeline of Enron stock price concerns in employee emails?
25. Find emails about the Arthur Andersen document shredding.
26. What happened with Enron Broadband Services?
27. When did Ken Lay send his last company-wide email?
28. What were the key milestones in the Enron-Dynegy merger discussions?
29. Track the evolution of energy trading concerns from 2000 to 2001.
30. When were employees told to report to work during the bankruptcy period?

## Category 4: Topics & Themes (тематический поиск)

31. What were the main legal concerns discussed in Enron emails?
32. Find discussions about ISDA contracts and trading agreements.
33. What natural gas trading strategies were discussed?
34. Find emails about employee stock options and 401k plans.
35. What compliance and regulatory issues were mentioned?
36. Find discussions about Enron's West Coast power trading.
37. What were the main HR-related topics in the email corpus?
38. Find emails discussing deals worth more than $1 million.
39. What technology systems and IT issues were discussed?
40. Find discussions about Enron's international operations.

## Category 5: Analysis & Reasoning (требует рассуждения)

41. Based on the emails, what warning signs existed before Enron's collapse?
42. How did the tone of internal emails change from early 2001 to December 2001?
43. What was the relationship between California energy crisis and Enron's trading?
44. Which departments seemed most aware of financial irregularities?
45. Compare the email patterns of executives vs regular employees during the crisis.
46. What external companies were most exposed to Enron's collapse based on email mentions?
47. Were there emails suggesting employees were told to hide information?
48. What was the impact of the bankruptcy on Enron's trading operations?
49. How did different departments react to the news of bankruptcy filing?
50. Based on email evidence, who were the key decision-makers in the final months?

---

## Scoring Template

| # | Question | Relevance (0-3) | Accuracy (0-3) | Citations (0/1) | Latency (s) | Notes |
|---|----------|-----------------|----------------|-----------------|-------------|-------|
| 1 | | | | | | |
| 2 | | | | | | |
| ... | | | | | | |

### Scoring Guide

**Relevance:**
- 0: Completely irrelevant answer
- 1: Tangentially related
- 2: Relevant but incomplete
- 3: Directly answers the question

**Accuracy:**
- 0: Factually wrong or hallucinated
- 1: Partially correct with errors
- 2: Mostly correct, minor issues
- 3: Fully accurate, verifiable from emails

**Citations:**
- 0: No email references
- 1: Cites specific emails (Message-ID, date, sender)
