# VHS script for the README GIF: https://github.com/charmbracelet/vhs
# Needs GROQ_API_KEY and a clone of a repo with agent history at ../litellm.
# Do one throwaway scan first so the embedding model is already downloaded and cached.
Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set TypingSpeed 40ms

Type "# rulewright reads git history and finds the mistakes agents keep repeating"
Enter
Sleep 1500ms

Type "rulewright scan --repo ../litellm --commits 500 --model groq/openai/gpt-oss-120b --yes"
Enter
# reading history, clustering, then the LLM writes each rule: allow up to two minutes
Sleep 120s

Type "# nothing is written until you accept a rule"
Enter
Sleep 1500ms

Type "rulewright apply <rule-id> --repo ../litellm"
Sleep 3s
