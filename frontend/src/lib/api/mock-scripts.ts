// GENERATED from services/goal-intelligence (fixture extractor) so mock mode returns
// exactly what the real service produces for these conversations. Do not hand-edit;
// regenerate if the GI compiler changes.
import type { GoalDraft } from "../contracts.ts";

export type MockStep = { user: string; draft: Omit<GoalDraft, "session_id" | "owner_user_id"> };
export type MockScript = { name: "product" | "procurement" | "unsupported"; steps: MockStep[] };

export const MOCK_SCRIPTS: MockScript[] = [
  {
    "name": "product",
    "steps": [
      {
        "user": "I want to buy an RTX 5070 for under €600",
        "draft": {
          "revision": 1,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "kind": "product_purchase",
              "max_total_eur_cents": 60000,
              "product": {
                "query": "RTX 5070"
              },
              "quantity": 1
            },
            "goal_type": "product_purchase",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 60
            },
            "title": "Buy RTX 5070 at or under EUR 600.00"
          },
          "missing_fields": [
            "include_shipping_and_tax",
            "delivery_country",
            "product_condition",
            "match_policy"
          ],
          "next_question": "Should your EUR 600.00 budget include shipping and taxes, with delivery to Finland?",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "yes",
        "draft": {
          "revision": 2,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "delivery_country": "FI",
              "include_shipping_and_tax": true,
              "kind": "product_purchase",
              "max_total_eur_cents": 60000,
              "product": {
                "query": "RTX 5070"
              },
              "quantity": 1
            },
            "goal_type": "product_purchase",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 60
            },
            "title": "Buy RTX 5070 at or under EUR 600.00 delivered"
          },
          "missing_fields": [
            "product_condition",
            "match_policy"
          ],
          "next_question": "Does it have to be new, or is used/refurbished fine too?",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "new only",
        "draft": {
          "revision": 3,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "delivery_country": "FI",
              "include_shipping_and_tax": true,
              "kind": "product_purchase",
              "max_total_eur_cents": 60000,
              "product": {
                "condition": "new",
                "query": "RTX 5070"
              },
              "quantity": 1
            },
            "goal_type": "product_purchase",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 60
            },
            "title": "Buy RTX 5070 at or under EUR 600.00 delivered"
          },
          "missing_fields": [
            "match_policy"
          ],
          "next_question": "Should I look for exactly RTX 5070, or are equivalent models OK?",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "equivalent models are fine",
        "draft": {
          "revision": 4,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "delivery_country": "FI",
              "include_shipping_and_tax": true,
              "kind": "product_purchase",
              "max_total_eur_cents": 60000,
              "product": {
                "condition": "new",
                "match_policy": "equivalent_allowed",
                "query": "RTX 5070"
              },
              "quantity": 1
            },
            "goal_type": "product_purchase",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 60
            },
            "title": "Buy RTX 5070 at or under EUR 600.00 delivered"
          },
          "missing_fields": [
            "summary_approval"
          ],
          "next_question": "Here's the goal: buy 1 × RTX 5070 (new, equivalent models OK) for at most EUR 600.00 in total including shipping and tax, delivered to Finland. I'll check offers every day, only trust evidence up to 1 hour old, notify you in the app, and prepare the purchase for you without ever placing an order. Shall I set it up like this? You can also change anything.",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "yes",
        "draft": {
          "revision": 5,
          "state": "ready_for_confirmation",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "delivery_country": "FI",
              "include_shipping_and_tax": true,
              "kind": "product_purchase",
              "max_total_eur_cents": 60000,
              "product": {
                "condition": "new",
                "match_policy": "equivalent_allowed",
                "query": "RTX 5070"
              },
              "quantity": 1
            },
            "goal_type": "product_purchase",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 60
            },
            "title": "Buy RTX 5070 at or under EUR 600.00 delivered"
          },
          "missing_fields": [],
          "next_question": null,
          "ready_for_confirmation": true
        }
      }
    ]
  },
  {
    "name": "procurement",
    "steps": [
      {
        "user": "Our café needs 20 L lactose-free milk and 10 kg coffee beans every month. We currently spend €250 and want to save 15%",
        "draft": {
          "revision": 1,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "cadence": "monthly",
              "current_total_eur_cents": 25000,
              "items": [
                {
                  "name": "Lactose-free milk",
                  "quantity": 20,
                  "unit": "L"
                },
                {
                  "name": "Coffee beans",
                  "quantity": 10,
                  "unit": "kg"
                }
              ],
              "kind": "recurring_procurement",
              "max_distinct_sellers": 3,
              "max_total_eur_cents": 21250
            },
            "goal_type": "recurring_procurement",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 240
            },
            "title": "Monthly supplies under EUR 212.50"
          },
          "missing_fields": [
            "items.requirements",
            "include_shipping_and_tax",
            "delivery_country"
          ],
          "next_question": "Any quality requirements for Lactose-free milk, Coffee beans (for example organic, lactose-free, whole beans)? Say 'none' if not.",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "milk: lactose free; beans: whole beans",
        "draft": {
          "revision": 2,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "cadence": "monthly",
              "current_total_eur_cents": 25000,
              "items": [
                {
                  "name": "Lactose-free milk",
                  "quantity": 20,
                  "requirements": [
                    "lactose free"
                  ],
                  "unit": "L"
                },
                {
                  "name": "Coffee beans",
                  "quantity": 10,
                  "requirements": [
                    "whole beans"
                  ],
                  "unit": "kg"
                }
              ],
              "kind": "recurring_procurement",
              "max_distinct_sellers": 3,
              "max_total_eur_cents": 21250
            },
            "goal_type": "recurring_procurement",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 240
            },
            "title": "Monthly supplies under EUR 212.50"
          },
          "missing_fields": [
            "include_shipping_and_tax",
            "delivery_country"
          ],
          "next_question": "Should your EUR 212.50 budget include shipping and taxes, with delivery to Finland?",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "Yes, delivered to Helsinki",
        "draft": {
          "revision": 3,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "cadence": "monthly",
              "current_total_eur_cents": 25000,
              "delivery_country": "FI",
              "include_shipping_and_tax": true,
              "items": [
                {
                  "name": "Lactose-free milk",
                  "quantity": 20,
                  "requirements": [
                    "lactose free"
                  ],
                  "unit": "L"
                },
                {
                  "name": "Coffee beans",
                  "quantity": 10,
                  "requirements": [
                    "whole beans"
                  ],
                  "unit": "kg"
                }
              ],
              "kind": "recurring_procurement",
              "max_distinct_sellers": 3,
              "max_total_eur_cents": 21250
            },
            "goal_type": "recurring_procurement",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 240
            },
            "title": "Monthly supplies under EUR 212.50"
          },
          "missing_fields": [
            "summary_approval"
          ],
          "next_question": "Here's the goal: every month, find 20 L Lactose-free milk (lactose free); 10 kg Coffee beans (whole beans) for at most EUR 212.50 including shipping and tax (you spend EUR 250.00 now), delivered to Finland, from at most 3 sellers. I'll check offers every day, only trust evidence up to 4 hours old, notify you in the app, and prepare the purchase for you without ever placing an order. Shall I set it up like this? You can also change anything.",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "sounds good",
        "draft": {
          "revision": 4,
          "state": "ready_for_confirmation",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "cadence": "monthly",
              "current_total_eur_cents": 25000,
              "delivery_country": "FI",
              "include_shipping_and_tax": true,
              "items": [
                {
                  "name": "Lactose-free milk",
                  "quantity": 20,
                  "requirements": [
                    "lactose free"
                  ],
                  "unit": "L"
                },
                {
                  "name": "Coffee beans",
                  "quantity": 10,
                  "requirements": [
                    "whole beans"
                  ],
                  "unit": "kg"
                }
              ],
              "kind": "recurring_procurement",
              "max_distinct_sellers": 3,
              "max_total_eur_cents": 21250
            },
            "goal_type": "recurring_procurement",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 240
            },
            "title": "Monthly supplies under EUR 212.50"
          },
          "missing_fields": [],
          "next_question": null,
          "ready_for_confirmation": true
        }
      }
    ]
  },
  {
    "name": "unsupported",
    "steps": [
      {
        "user": "Book me a flight to Rome under 200 EUR",
        "draft": {
          "revision": 1,
          "state": "collecting",
          "candidate_goal": {
            "unsupported_request": "Travel"
          },
          "missing_fields": [
            "supported_goal_type"
          ],
          "next_question": "Travel can't be tracked yet. I can watch for a product you want to buy, or for monthly supplies for your business. What would you like to track?",
          "ready_for_confirmation": false
        }
      },
      {
        "user": "I want to buy a Kindle Paperwhite",
        "draft": {
          "revision": 2,
          "state": "collecting",
          "candidate_goal": {
            "actions": {
              "agent_handoff": "prepare_only",
              "auto_purchase": false,
              "notify_user": true
            },
            "criteria": {
              "kind": "product_purchase",
              "max_total_eur_cents": 20000,
              "product": {
                "query": "Kindle Paperwhite"
              },
              "quantity": 1
            },
            "goal_type": "product_purchase",
            "monitoring": {
              "frequency_minutes": 1440,
              "max_evidence_age_minutes": 60
            },
            "title": "Buy Kindle Paperwhite at or under EUR 200.00"
          },
          "missing_fields": [
            "include_shipping_and_tax",
            "delivery_country",
            "product_condition",
            "match_policy"
          ],
          "next_question": "Should your EUR 200.00 budget include shipping and taxes, with delivery to Finland?",
          "ready_for_confirmation": false
        }
      }
    ]
  }
];
