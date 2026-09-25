(require 'minuet)

(setq minuet-provider 'openai-fim-compatible
      minuet-n-completions 1
      minuet-request-timeout 10)

(plist-put minuet-openai-fim-compatible-options :name "Claude Bridge")
(plist-put minuet-openai-fim-compatible-options :end-point "http://127.0.0.1:11435/v1/completions")
(plist-put minuet-openai-fim-compatible-options :api-key (lambda () "bridge"))
(plist-put minuet-openai-fim-compatible-options :model "claude-haiku")
(plist-put minuet-openai-fim-compatible-options :template
           (list :prompt (lambda (ctx)
                           (concat "<fim_prefix>" (plist-get ctx :before-cursor)
                                   "<fim_suffix>" (plist-get ctx :after-cursor)
                                   "<fim_middle>"))
                 :suffix nil))

(add-hook 'prog-mode-hook #'minuet-auto-suggestion-mode)
