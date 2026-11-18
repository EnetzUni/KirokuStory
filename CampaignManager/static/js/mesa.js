/* Mesa de campaña · interacción con jQuery.
   Without JavaScript every action still works: forms post and the page reloads.
   With it, forms marked data-ajax are sent with $.post and the server answers
   JSON with the HTML of the part that changed, rendered with the same partial
   template the page uses. */
(function ($) {
  "use strict";

  document.documentElement.classList.add("js");

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : $("input[name=csrfmiddlewaretoken]").first().val();
  }

  $.ajaxSetup({
    headers: { "X-Requested-With": "XMLHttpRequest" },
    beforeSend: function (xhr, settings) {
      if (!/^(GET|HEAD|OPTIONS)$/i.test(settings.type)) {
        xhr.setRequestHeader("X-CSRFToken", csrfToken());
      }
    }
  });

  // ---- Menú plegable en el móvil ----
  $(document).on("click", ".boton-menu", function () {
    var nav = $("#" + $(this).attr("aria-controls"));
    var open = !nav.hasClass("abierta");
    nav.toggleClass("abierta", open);
    $(this).attr("aria-expanded", open ? "true" : "false");
  });

  // ---- Formularios por AJAX ----
  // The button that sent the form, so its name/value travels with the data.
  // ":submit" also matches <button> without a type attribute (submit by default);
  // "[type=submit]" only matches the attribute, so it missed most buttons.
  $(document).on("click", "form[data-ajax] :submit", function () {
    $(this).closest("form").data("submitter", this);
  });

  function valuesOf($root) {
    var out = {};
    $root.find("[data-valor]").each(function () { out[$(this).data("valor")] = $(this).text().trim(); });
    return out;
  }

  function highlightChanges($root, before) {
    $root.find("[data-valor]").each(function () {
      var key = $(this).data("valor");
      if (key in before && before[key] !== $(this).text().trim()) {
        $(this).removeClass("cambio");
        void this.offsetWidth; // restart the animation
        $(this).addClass("cambio");
      }
    });
  }

  // Panels (<details>) that were open stay open when their part is replaced.
  function keepOpen($old, $new) {
    $old.find("details[open][id]").each(function () { $new.find("#" + this.id).prop("open", true); });
  }

  function applyResponse(data, $form) {
    if (data.redirect) { window.location.href = data.redirect; return; }
    var target = data.target || $form.data("target");
    if (target && data.html !== undefined) {
      var $old = $(target);
      var before = valuesOf($old);
      var $new = $(data.html);
      keepOpen($old, $new);
      $old.replaceWith($new);
      highlightChanges($new, before);
      $new.trigger("mesa:actualizado");
    }
    var $msg = $(data.msgTarget || $form.data("msg") || "#mensaje-ajax");
    if ($msg.length) {
      $msg.text(data.msg || "").toggleClass("error", !!data.error).prop("hidden", !data.msg);
      if ($msg.hasClass("flotante")) {
        clearTimeout($msg.data("timer"));
        $msg.data("timer", setTimeout(function () { $msg.prop("hidden", true); }, 5000));
      }
    }
  }

  $(document).on("submit", "form[data-ajax]", function (event) {
    var $form = $(this);
    if ($form.find("input[type=file]").filter(function () { return this.files.length; }).length) {
      return; // file uploads go the normal way
    }
    event.preventDefault();
    var data = $form.serializeArray();
    var submitter = (event.originalEvent && event.originalEvent.submitter) || $form.data("submitter");
    if (submitter && submitter.name) { data.push({ name: submitter.name, value: submitter.value }); }
    $form.removeData("submitter");
    $form.find(":submit").prop("disabled", true);
    $.post($form.attr("action") || window.location.href, $.param(data))
      .done(function (response) { applyResponse(response, $form); })
      .fail(function (xhr) {
        var message = (xhr.responseJSON && xhr.responseJSON.msg) || "No se ha podido guardar. Vuelve a intentarlo.";
        applyResponse({ msg: message, error: true }, $form);
      })
      .always(function () { $form.find(":submit").prop("disabled", false); });
  });

  // Links that load a part of the page (selecting a note on the board).
  $(document).on("click", "a[data-ajax-link]", function (event) {
    var link = this;
    event.preventDefault();
    $.get(link.href).done(function (response) {
      applyResponse(response, $(link));
      if (window.history && history.replaceState) { history.replaceState(null, "", link.href); }
    }).fail(function () { window.location.href = link.href; });
  });

  // GET forms that reload a part (the map's session slider).
  $(document).on("submit", "form[data-ajax-get]", function (event) {
    var $form = $(this);
    event.preventDefault();
    var url = ($form.attr("action") || window.location.pathname) + "?" + $form.serialize();
    $.get(url).done(function (response) {
      applyResponse(response, $form);
      if (window.history && history.replaceState) { history.replaceState(null, "", url); }
    });
  });
  $(document).on("change", "form[data-ajax-get] input[type=range]", function () {
    $(this).closest("form").trigger("submit");
  });

  // Timeline: show an event's detail without reloading.
  $(document).on("click", "a[data-detalle]", function (event) {
    event.preventDefault();
    var $root = $(this).closest("#linea");
    $root.find(".tl-detalle").prop("hidden", true);
    $root.find(".tl-tarjeta").removeAttr("aria-current");
    $(this).attr("aria-current", "true");
    $($(this).data("detalle")).prop("hidden", false);
  });

  // Inputs that save on change (notes, initiative...).
  $(document).on("change", "form[data-ajax][data-auto] :input", function () {
    $(this).closest("form").trigger("submit");
  });

  // ---- Vista previa de [[enlaces]] ----
  var $preview = $('<div class="vista-previa" role="tooltip" hidden></div>').appendTo("body");
  var previewCache = {};
  var pressTimer = null;

  function showPreview(link) {
    var url = $(link).data("preview");
    if (!url) { return; }
    function draw(data) {
      $preview.empty()
        .append($("<strong>").text(data.title))
        .append($("<span>").text(data.summary || "Página sin resumen."));
      var offset = $(link).offset();
      $preview.css({ left: Math.max(8, Math.min(offset.left, $(window).width() - 336)), top: offset.top + $(link).outerHeight() + 6 })
        .prop("hidden", false);
    }
    if (previewCache[url]) { draw(previewCache[url]); return; }
    $.getJSON(url).done(function (data) { previewCache[url] = data; draw(data); });
  }
  function hidePreview() { $preview.prop("hidden", true); }

  $(document).on("mouseenter focus", "a.wikilink[data-preview]", function () { showPreview(this); });
  $(document).on("mouseleave blur", "a.wikilink[data-preview]", hidePreview);
  $(document).on("touchstart", "a.wikilink[data-preview]", function () {
    var link = this;
    pressTimer = setTimeout(function () { showPreview(link); }, 450);
  });
  $(document).on("touchend touchmove", "a.wikilink[data-preview]", function () { clearTimeout(pressTimer); });

  // ---- Colocar en el corcho o en el mapa ----
  // A click on the surface fills the x/y fields of the form and sends it.
  $(document).on("click", "[data-colocar]", function (event) {
    var $surface = $(this);
    var $form = $($surface.data("colocar"));
    if (!$form.length || $(event.target).closest("a, button").length) { return; }
    var rect = this.getBoundingClientRect();
    var mode = $surface.data("unidad"); // "px" (board 1100x640) or "%" (map)
    var x, y;
    if (mode === "%") {
      x = ((event.clientX - rect.left) / rect.width) * 100;
      y = ((event.clientY - rect.top) / rect.height) * 100;
      x = Math.round(Math.max(2, Math.min(98, x)) * 10) / 10;
      y = Math.round(Math.max(2, Math.min(98, y)) * 10) / 10;
    } else {
      x = Math.round((event.clientX - rect.left) * (1100 / rect.width) - 100);
      y = Math.round((event.clientY - rect.top) * (640 / rect.height) - 20);
      x = Math.max(8, Math.min(892, x));
      y = Math.max(8, Math.min(512, y));
    }
    $form.find("[name=x]").val(x);
    $form.find("[name=y]").val(y);
    if ($form.data("enviar")) { $form.trigger("submit"); }
    else { $form.trigger("mesa:colocado"); $form.find("input[type=text]").first().trigger("focus"); }
  });

  // ---- Creador de personajes ----
  // Only the lineages of the chosen race (without JS, all are shown and the server checks them).
  function showLineages() {
    var race = $("input[data-raza]:checked").val();
    $(".linaje-grupo").each(function () {
      var mine = String($(this).data("de-raza")) === race;
      $(this).prop("hidden", !mine);
      if (!mine) { $(this).find("input").prop("checked", false); }
    });
  }
  if ($(".linaje-grupo").length) { showLineages(); }
  $(document).on("change", "input[data-raza]", showLineages);

  // Point buy: points left and modifiers while typing (the server checks them again).
  var POINT_COST = { 8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9 };
  $(document).on("input", "[data-reparto] input", function () {
    var $box = $(this).closest("[data-reparto]");
    var spent = 0;
    $box.find("input").each(function () {
      var value = parseInt(this.value, 10);
      if (value in POINT_COST) { spent += POINT_COST[value]; }
      var mod = isNaN(value) ? 0 : Math.floor((value - 10) / 2);
      $(this).siblings("[data-mod]").text(mod >= 0 ? "+" + mod : "\u2212" + Math.abs(mod));
    });
    $("[data-puntos]").text(parseInt($box.data("total"), 10) - spent);
  });

  // ---- Refresco periódico (turnos) ----
  $("[data-refrescar]").each(function () {
    var $box = $(this);
    var url = $box.data("refrescar");
    var seconds = parseInt($box.data("cada"), 10) || 5;
    setInterval(function () {
      if (document.hidden || $box.find(":focus").length) { return; }
      $.get(url).done(function (data) {
        if (data.html === undefined) { return; }
        var $current = $("#" + $box.attr("id"));
        var before = valuesOf($current);
        var $new = $(data.html);
        keepOpen($current, $new);
        $current.replaceWith($new);
        highlightChanges($new, before);
        $box = $new;
      });
    }, seconds * 1000);
  });
})(jQuery);
