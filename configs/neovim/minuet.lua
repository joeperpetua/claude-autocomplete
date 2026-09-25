require("minuet").setup({
  provider = "openai_fim_compatible",
  n_completions = 1,
  request_timeout = 10,
  debounce = 250,
  provider_options = {
    openai_fim_compatible = {
      name = "Claude Bridge",
      end_point = "http://127.0.0.1:11435/v1/completions",
      api_key = function()
        return "bridge"
      end,
      model = "claude-haiku",
      stream = true,
      template = {
        prompt = function(context_before_cursor, context_after_cursor, _)
          return "<fim_prefix>" .. context_before_cursor .. "<fim_suffix>" .. context_after_cursor .. "<fim_middle>"
        end,
        suffix = false,
      },
    },
  },
  virtualtext = {
    auto_trigger_ft = { "*" },
  },
})
