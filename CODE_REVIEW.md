### **ChatGPT API Usage Incorrect**
**Problem:** The API calls use wrong method signature.

**File:** [src/ntu_easy_llm/core/utils.py](src/ntu_easy_llm/core/utils.py#L82-L88)

**Current (WRONG):**
```python
client.responses.create(
    model=model_name,
    input=prompt  # ❌ Wrong parameter
)
```

**Should be:**
```python
client.chat.completions.create(
    model=model_name,
    messages=[{"role": "user", "content": prompt}]  # ✅ Correct
)
```

This is why ChatGPT calls fail - `responses.create()` doesn't exist in OpenAI SDK v2+.

---

## 📋 SUMMARY OF CHANGES NEEDED

| Issue | Severity | Files | Fix |
|-------|----------|-------|-----|
| ChatGPT API wrong | 🔴 CRITICAL | utils.py | Change to `chat.completions.create()` |

---