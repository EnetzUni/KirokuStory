/* Kiroku Story · interacción con jQuery.
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

  // jQuery effects (fadeIn, fadeOut...) are switched off for whoever asks the system for less motion.
  var lessMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  if (lessMotion) { $.fx.off = lessMotion.matches; }

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

  // ---- Secciones plegables que se recuerdan (menú del Mundo, aventuras terminadas) ----
  // <details data-recordar="key">: the browser keeps whether it was left open. The section
  // being read (data-aqui) always opens. Storage can fail (private mode): then nothing is kept.
  function remembered(key) {
    try { return window.localStorage.getItem("plegable:" + key); } catch (e) { return null; }
  }
  function remember(key, open) {
    try { window.localStorage.setItem("plegable:" + key, open ? "1" : "0"); } catch (e) { /* not kept */ }
  }
  function restoreFolds(root) {
    $(root).find("details[data-recordar]").each(function () {
      var saved = remembered($(this).data("recordar"));
      if (saved !== null && !this.hasAttribute("data-aqui")) {
        this.open = saved === "1";
      }
    });
  }
  restoreFolds(document);
  $("[data-plegar-todo]").prop("hidden", false);
  // «toggle» does not bubble, so it is caught on its way down (capture), also for parts sent by AJAX.
  document.addEventListener("toggle", function (event) {
    var target = event.target;
    if (target.matches && target.matches("details[data-recordar]")) {
      remember(target.getAttribute("data-recordar"), target.open);
    }
  }, true);
  $(document).on("click", "[data-plegar]", function () {
    var open = $(this).data("plegar") === "abrir";
    $(this).closest("nav, section, [data-plegables]").find("details[data-recordar], details.seccion-arbol").each(function () {
      this.open = open;
    });
  });
  // A link to something inside a folded section (#aventura-3, #sesion-12) opens the folds around it.
  function openHashTarget() {
    if (!window.location.hash) return;
    var target = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    if (!target) return;
    var folds = $(target).parents("details").addBack("details");
    if (!folds.length) return;
    folds.prop("open", true);
    target.scrollIntoView();
  }
  openHashTarget();
  $(window).on("hashchange", openHashTarget);


  // ---- Formularios por AJAX ----
  // The button that sent the form, so its name/value travels with the data.
  // ":submit" also matches <button> without a type attribute (submit by default);
  // "[type=submit]" only matches the attribute, so it missed most buttons.
  $(document).on("click", "form[data-ajax] :submit", function () {
    $(this).closest("form").data("submitter", this);
  });

  // Buttons that delete something ask first (data-confirmar="the question").
  $(document).on("click", "[data-confirmar]", function (event) {
    if (!window.confirm($(this).data("confirmar"))) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
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
      // The sheet: remember what this version shows, so the refresh below knows when it changes.
      if (data.version) { $("#ficha-marco").attr("data-version", data.version); }
    }
    var $msg = $(data.msgTarget || $form.data("msg") || "#mensaje-ajax");
    if ($msg.length) {
      $msg.text(data.msg || "").toggleClass("error", !!data.error);
      if ($msg.hasClass("flotante")) {
        // Effect: the floating message fades in and, 5 s later, fades out (jQuery fadeIn / fadeOut).
        clearTimeout($msg.data("timer"));
        $msg.stop(true, true);
        if (data.msg) {
          $msg.prop("hidden", false).hide().fadeIn(200);
          $msg.data("timer", setTimeout(function () {
            $msg.fadeOut(400, function () { $msg.prop("hidden", true).css("display", ""); });
          }, 5000));
        } else {
          $msg.prop("hidden", true).css("display", "");
        }
      } else {
        $msg.prop("hidden", !data.msg);
      }
    }
  }

  $(document).on("submit", "form[data-ajax]", function (event) {
    var $form = $(this);
    $form.trigger("mesa:enviar"); // last chance to fill fields (e.g. where a new note goes)
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
      if (window.history && history.replaceState) { history.replaceState(history.state, "", link.href); }
    }).fail(function () { window.location.href = link.href; });
  });

  // GET forms that reload a part (the map's session slider).
  $(document).on("submit", "form[data-ajax-get]", function (event) {
    var $form = $(this);
    event.preventDefault();
    var url = ($form.attr("action") || window.location.pathname) + "?" + $form.serialize();
    $.get(url).done(function (response) {
      applyResponse(response, $form);
      if (window.history && history.replaceState) { history.replaceState(history.state, "", url); }
    });
  });
  $(document).on("change", "form[data-ajax-get] input[type=range]", function () {
    $(this).closest("form").trigger("submit");
  });

  // The map's two-handle slider: «desde» never passes «hasta». While dragging, the numbers
  // and the coloured stretch follow; the route reloads when the handle is let go («change»).
  function rangeEnds($box) {
    var $since = $box.find("[data-extremo=desde]"), $until = $box.find("[data-extremo=hasta]");
    return { since: $since, until: $until, max: parseInt($until.attr("max"), 10) };
  }
  function paintRange($box) {
    var ends = rangeEnds($box);
    var since = parseInt(ends.since.val(), 10), until = parseInt(ends.until.val(), 10);
    var pct = function (n) { return ends.max > 1 ? ((n - 1) * 100 / (ends.max - 1)) + "%" : "0%"; };
    $box[0].style.setProperty("--desde", pct(since));
    $box[0].style.setProperty("--hasta", pct(until));
    var $form = $box.closest("form");
    $form.find("[data-rango-desde]").text(since);
    $form.find("[data-rango-hasta]").text(until);
    // When both handles meet, the one that can still move goes on top.
    ends.since.toggleClass("encima", since === until && since > ends.max / 2);
  }
  $(document).on("input", "[data-doble-rango] input[type=range]", function () {
    var $box = $(this).closest("[data-doble-rango]"), ends = rangeEnds($box);
    if (parseInt(ends.since.val(), 10) > parseInt(ends.until.val(), 10)) {
      // Keep the order: the handle being moved stops at the other one.
      $(this).val($(this).is(ends.since) ? ends.until.val() : ends.since.val());
    }
    paintRange($box);
  });
  $("[data-doble-rango]").each(function () { paintRange($(this)); });
  $(document).on("mesa:actualizado", function (event) {
    $(event.target).find("[data-doble-rango]").addBack("[data-doble-rango]").each(function () { paintRange($(this)); });
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

  // ---- Subir la imagen del mapa ----
  // Choosing a file shows it at once in the map and sends the form (a normal
  // multipart POST: the page reloads with the saved image).
  $(document).on("change", "form[data-subir-imagen] input[type=file]", function () {
    var file = this.files && this.files[0];
    if (!file) { return; }
    var form = this.form;
    if (form.checkValidity && !form.checkValidity()) { form.reportValidity(); return; }
    var $surface = $($(form).data("subir-imagen")).first();
    if ($surface.length && window.URL && URL.createObjectURL) {
      $surface.find(".vacio").remove();
      var $img = $surface.children("img");
      if (!$img.length) { $img = $("<img>").prependTo($surface); }
      $img.attr({ src: URL.createObjectURL(file), alt: file.name });
    }
    $(form).find(":submit").prop("disabled", true).text("Subiendo…");
    if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
  });

  // ---- Zoom: mapa y pizarra ----
  // Only inside a zoomable window ([data-zoom]): wheel / trackpad pinch,
  // two-finger pinch, drag to move, double click, the +/− buttons and the keys
  // + − 0 and arrows (with the window focused). Two kinds of layer inside:
  //  - map (.mapa-lienzo with an image): the layer gets its real width and
  //    height in pixels, never a CSS scale. The browser then draws the image
  //    from the original file at exactly the size on screen, so a good PNG
  //    stays sharp up to its own resolution. The image keeps its proportions
  //    (fitted, not stretched) and pins stay in % of the image.
  //  - board ([data-zoom-capa] with data-ancho / data-alto): notes and threads
  //    are placed in board pixels, so the board is scaled with a transform
  //    (without will-change, so the text is redrawn sharp at each zoom level).
  var ZOOM_STEP = 1.6;

  // ---- Pantalla completa (mapa y pizarra) ----
  // The whole page goes into the browser's full screen (when there is one: not on
  // iPhone) and the window is stretched over it with a class. So the full screen
  // survives when a click on a pin or a note re-renders the section: the new window
  // gets the class again (initZoom). It closes with «✕ Salir», the button, Esc, or
  // the phone's «back»: entering adds a history entry, and going back closes it.
  var pantalla = { key: null };

  function fullscreenView() {
    return pantalla.key ? document.querySelector('[data-zoom="' + pantalla.key + '"]') : null;
  }

  function showFullscreen(view, on) {
    view.classList.toggle("zoom-pantalla", on);
    var $button = $(view).find("[data-zoom-accion=pantalla]");
    var label = $button.data(on ? "etiqueta-cerrar" : "etiqueta-abrir");
    $button.attr({ "aria-pressed": on ? "true" : "false", "aria-label": label, title: label });
  }

  function enterFullscreen(view) {
    if (pantalla.key) { return; }
    pantalla.key = view.getAttribute("data-zoom");
    document.documentElement.classList.add("pantalla-abierta");
    showFullscreen(view, true);
    var root = document.documentElement;
    if (root.requestFullscreen) {
      var request = root.requestFullscreen({ navigationUI: "hide" });
      if (request && request.catch) { request.catch(function () { /* not allowed: the overlay is enough */ }); }
    }
    try { history.pushState({ mesaPantalla: pantalla.key }, ""); } catch (err) { /* no history: Esc and the button still work */ }
    view.focus({ preventScroll: true }); // so + − 0 and the arrows work straight away
  }

  function exitFullscreen(fromHistory) {
    if (!pantalla.key) { return; }
    var view = fullscreenView();
    pantalla.key = null;
    document.documentElement.classList.remove("pantalla-abierta");
    if (view) { showFullscreen(view, false); }
    if (document.fullscreenElement && document.exitFullscreen) {
      var leave = document.exitFullscreen();
      if (leave && leave.catch) { leave.catch(function () {}); }
    }
    // Take away the history entry added on entering (unless «back» already did).
    if (!fromHistory && history.state && history.state.mesaPantalla) { history.back(); }
  }

  window.addEventListener("popstate", function () { if (pantalla.key) { exitFullscreen(true); } });
  document.addEventListener("fullscreenchange", function () {
    if (!document.fullscreenElement && pantalla.key) { exitFullscreen(false); } // Esc in the browser's full screen
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && pantalla.key) { e.preventDefault(); exitFullscreen(false); } // the overlay (no browser full screen)
  });
  var zoomMemory = {}; // window key -> {z, fx, fy}: survives the AJAX re-render of a section

  function initZoom(view) {
    if (view.zoomListo) { return; }
    var layer = view.querySelector("[data-zoom-capa], .mapa-lienzo");
    if (!layer) { return; }
    var scaled = layer.hasAttribute("data-ancho");
    var img = scaled ? null : layer.querySelector("img");
    if (!scaled && !img) { return; }
    view.zoomListo = true;
    var key = view.getAttribute("data-zoom");
    var $buttons = $(view).children(".zoom-botones");
    if (pantalla.key === key) { showFullscreen(view, true); }
    var s = { z: 1, left: 0, top: 0, bw: 0, bh: 0, max: 4 };
    var ready = false, frame = null;

    function dpr() { return window.devicePixelRatio || 1; }
    // Whole device pixels: no half-pixel offsets that would soften the image.
    function snap(v) { return Math.round(v * dpr()) / dpr(); }
    function natural() {
      return scaled ? { w: +layer.getAttribute("data-ancho"), h: +layer.getAttribute("data-alto") }
                    : { w: img.naturalWidth, h: img.naturalHeight };
    }

    // Size of the whole layer fitted in the window (zoom 1) and the zoom limit.
    function fit() {
      var vw = view.clientWidth, vh = view.clientHeight, n = natural();
      var ratio = n.w && n.h ? n.w / n.h : vw / vh;
      if (ratio > vw / vh) { s.bw = vw; s.bh = vw / ratio; } else { s.bh = vh; s.bw = vh * ratio; }
      if (scaled) {
        s.max = Math.min(16, Math.max(2, 2 * n.w / s.bw)); // up to twice the board's real size
      } else {
        // Up to twice the image's own resolution (one image pixel = 2 screen pixels), never less than x4.
        s.max = Math.min(24, Math.max(4, n.w ? 2 * n.w / (s.bw * dpr()) : 4));
      }
    }

    function clamp() {
      var vw = view.clientWidth, vh = view.clientHeight, w = s.bw * s.z, h = s.bh * s.z;
      s.left = w <= vw ? (vw - w) / 2 : Math.min(0, Math.max(vw - w, s.left));
      s.top = h <= vh ? (vh - h) / 2 : Math.min(0, Math.max(vh - h, s.top));
    }

    function paint() {
      frame = null;
      var st = layer.style;
      if (scaled) {
        st.transform = "translate(" + snap(s.left) + "px," + snap(s.top) + "px) scale(" + (s.bw * s.z / natural().w) + ")";
        return;
      }
      st.right = st.bottom = "auto";
      st.left = snap(s.left) + "px";
      st.top = snap(s.top) + "px";
      st.width = snap(s.bw * s.z) + "px";
      st.height = snap(s.bh * s.z) + "px";
    }

    function draw() {
      clamp();
      if (!frame) { frame = window.requestAnimationFrame(paint); }
      var zoomed = s.z > 1.001;
      view.classList.toggle("ampliado", zoomed);
      $buttons.find("[data-zoom-accion=menos], [data-zoom-accion=entero]").prop("disabled", !zoomed);
      $buttons.find("[data-zoom-accion=mas]").prop("disabled", s.z >= s.max - 0.001);
      zoomMemory[key] = { z: s.z, fx: (view.clientWidth / 2 - s.left) / (s.bw * s.z),
                          fy: (view.clientHeight / 2 - s.top) / (s.bh * s.z) };
    }

    // Zoom keeping the point (px, py) of the window still.
    function zoomAt(z, px, py) {
      z = Math.max(1, Math.min(s.max, z));
      var fx = (px - s.left) / (s.bw * s.z), fy = (py - s.top) / (s.bh * s.z);
      s.z = z;
      s.left = px - fx * s.bw * z;
      s.top = py - fy * s.bh * z;
      draw();
    }

    // Show the point (fx, fy) of the layer (0..1) in the middle of the window.
    function centreOn(z, fx, fy) {
      s.z = Math.max(1, Math.min(s.max, z));
      s.left = view.clientWidth / 2 - fx * s.bw * s.z;
      s.top = view.clientHeight / 2 - fy * s.bh * s.z;
      draw();
    }

    function local(e) {
      var r = view.getBoundingClientRect();
      return { x: e.clientX - r.left - view.clientLeft, y: e.clientY - r.top - view.clientTop };
    }

    // What the window is looking at, in the layer's own units (board pixels or image pixels).
    view.zoomCentro = function () {
      var m = zoomMemory[key] || { fx: 0.5, fy: 0.5 }, n = natural();
      return { x: m.fx * n.w, y: m.fy * n.h };
    };

    function start() {
      if (ready) { return; }
      ready = true;
      fit();
      var saved = zoomMemory[key];
      if (saved) { centreOn(saved.z, saved.fx, saved.fy); } else { centreOn(1, 0.5, 0.5); }
      if (frame) { window.cancelAnimationFrame(frame); }
      paint(); // now, so the layout without JavaScript is never seen
    }
    if (scaled || img.complete) { start(); } else { img.addEventListener("load", start); img.addEventListener("error", start); }

    // Window resized (or the layout changed): same zoom, same point in the middle.
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        if (!ready) { return; }
        var m = zoomMemory[key];
        fit();
        centreOn(s.z, m.fx, m.fy);
      }).observe(view);
    }

    // Mouse wheel and trackpad pinch (which arrives as wheel + ctrlKey).
    // Fully zoomed out, scrolling down still scrolls the page.
    view.addEventListener("wheel", function (e) {
      var dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? view.clientHeight : 1);
      if (!ready || (dy > 0 && s.z <= 1.001)) { return; }
      e.preventDefault();
      var p = local(e);
      zoomAt(s.z * Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015)), p.x, p.y);
    }, { passive: false });

    // Drag with one pointer, pinch with two.
    var pointers = {}, startPoint = null, last = null, moved = false;
    function gesture() {
      var ids = Object.keys(pointers), x = 0, y = 0, d = 0;
      ids.forEach(function (id) { x += pointers[id].x; y += pointers[id].y; });
      if (ids.length > 1) { d = Math.hypot(pointers[ids[0]].x - pointers[ids[1]].x, pointers[ids[0]].y - pointers[ids[1]].y); }
      return { n: ids.length, x: x / (ids.length || 1), y: y / (ids.length || 1), d: d };
    }
    view.addEventListener("pointerdown", function (e) {
      if (Object.keys(pointers).length === 0) { moved = false; }
      if (!ready || (e.pointerType === "mouse" && e.button !== 0) || $(e.target).closest(".zoom-botones, .zoom-salir").length) { return; }
      pointers[e.pointerId] = local(e);
      if (!startPoint) { startPoint = pointers[e.pointerId]; }
      last = gesture();
    });
    view.addEventListener("pointermove", function (e) {
      if (!pointers[e.pointerId]) { return; }
      pointers[e.pointerId] = local(e);
      var g = gesture();
      if (!moved) {
        // A click on a pin or a note (or on the surface while placing) is not a drag.
        if (g.n < 2 && Math.hypot(g.x - startPoint.x, g.y - startPoint.y) < 5) { return; }
        moved = true;
        view.classList.add("arrastrando");
        Object.keys(pointers).forEach(function (id) { try { view.setPointerCapture(+id); } catch (err) { /* already gone */ } });
      }
      if (g.n !== last.n) { last = g; return; }
      s.left += g.x - last.x;
      s.top += g.y - last.y;
      if (g.n > 1 && last.d) { zoomAt(s.z * g.d / last.d, g.x, g.y); } else { draw(); }
      last = g;
    });
    function release(e) {
      if (!pointers[e.pointerId]) { return; }
      delete pointers[e.pointerId];
      last = gesture();
      if (!last.n) {
        startPoint = null;
        view.classList.remove("arrastrando");
        // The click that follows a drag comes right after pointerup; later clicks (keyboard too) are normal again.
        if (moved) { setTimeout(function () { moved = false; }, 0); }
      }
    }
    view.addEventListener("pointerup", release);
    view.addEventListener("pointercancel", release);
    // After a drag, the click that follows must not open a pin or a note, or place anything.
    view.addEventListener("click", function (e) {
      if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; }
    }, true);
    // Dragging from a link must move the window, not start the browser's link drag.
    view.addEventListener("dragstart", function (e) { e.preventDefault(); });

    view.addEventListener("dblclick", function (e) {
      if (!ready || view.classList.contains("colocando") || layer.classList.contains("colocando") ||
          $(e.target).closest("a, button").length) { return; }
      var p = local(e);
      zoomAt(s.z * 2, p.x, p.y);
    });

    $(view).on("click", "[data-zoom-accion]", function () {
      var action = $(this).data("zoom-accion");
      if (action === "pantalla") { if (pantalla.key === key) { exitFullscreen(false); } else { enterFullscreen(view); } return; }
      if (action === "salir") { exitFullscreen(false); return; }
      if (action === "entero") { centreOn(1, 0.5, 0.5); return; }
      zoomAt(s.z * (action === "mas" ? ZOOM_STEP : 1 / ZOOM_STEP), view.clientWidth / 2, view.clientHeight / 2);
    });

    view.addEventListener("keydown", function (e) {
      if (e.target !== view || !ready) { return; }
      var cx = view.clientWidth / 2, cy = view.clientHeight / 2, step = 60;
      switch (e.key) {
        case "+": case "=": zoomAt(s.z * ZOOM_STEP, cx, cy); break;
        case "-": case "_": zoomAt(s.z / ZOOM_STEP, cx, cy); break;
        case "0": centreOn(1, 0.5, 0.5); break;
        case "ArrowLeft": s.left += step; draw(); break;
        case "ArrowRight": s.left -= step; draw(); break;
        case "ArrowUp": s.top += step; draw(); break;
        case "ArrowDown": s.top -= step; draw(); break;
        default: return;
      }
      e.preventDefault();
    });
  }

  // ---- Editor del camino (máster) ----
  // Each stretch of the route (previous stop → waypoints → stop) is one
  // polyline with data-guardar. Its waypoints get handles to drag; halfway
  // along each part there is a "+" that adds a point (click it, or drag it
  // straight away). Double click, right click or Supr on a point removes it.
  // Every change is saved by itself. Points are in % of the image, so they
  // follow the zoom.
  function initRouteEditor(layer) {
    if (layer.rutaLista) { return; }
    var lines = layer.querySelectorAll("polyline[data-guardar]");
    if (!lines.length) { return; }
    layer.rutaLista = true;
    Array.prototype.forEach.call(lines, function (line) { routeStretch(layer, line); });
  }

  function routeStretch(layer, line) {
    var pts = line.getAttribute("points").trim().split(/\s+/).map(function (pair) {
      var xy = pair.split(",");
      return [parseFloat(xy[0]), parseFloat(xy[1])];
    });
    var arrow = layer.querySelector('[data-flecha="' + line.getAttribute("data-tramo") + '"]');
    var handles = [], saveTimer = null;

    function r2(v) { return Math.round(v * 100) / 100; }
    function inside(v) { return Math.max(0, Math.min(100, v)); }
    function place(el, p) { el.style.left = p[0] + "%"; el.style.top = p[1] + "%"; }
    function middle(i) { return [r2((pts[i][0] + pts[i + 1][0]) / 2), r2((pts[i][1] + pts[i + 1][1]) / 2)]; }

    function drawLine() {
      line.setAttribute("points", pts.map(function (p) { return p[0] + "," + p[1]; }).join(" "));
      if (!arrow) { return; }
      for (var i = pts.length - 1; i > 0; i--) {
        var dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
        if (Math.abs(dx) > 0.01 || Math.abs(dy) > 0.01) {
          place(arrow, pts[i]);
          arrow.style.setProperty("--dx", r2(dx));
          arrow.style.setProperty("--dy", r2(dy));
          arrow.hidden = false;
          return;
        }
      }
      arrow.hidden = true;
    }

    function positionHandles() {
      handles.forEach(function (h) { place(h.el, h.kind === "punto" ? pts[h.i] : middle(h.i)); });
    }

    function save(delay) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        var inner = pts.slice(1, -1).map(function (p) { return p[0] + "," + p[1]; }).join(" ");
        $.post(line.getAttribute("data-guardar"), { puntos: inner })
          .done(function (response) { applyResponse(response, $(line)); })
          .fail(function (xhr) {
            var message = (xhr.responseJSON && xhr.responseJSON.msg) || "No se ha podido guardar el camino. Vuelve a intentarlo.";
            applyResponse({ msg: message, error: true }, $(line));
          });
      }, delay);
    }

    function pointFor(i) {
      for (var k = 0; k < handles.length; k++) { if (handles[k].kind === "punto" && handles[k].i === i) { return handles[k]; } }
      return null;
    }

    function build(focusIndex) {
      handles.forEach(function (h) { h.el.parentNode.removeChild(h.el); });
      handles = [];
      for (var i = 0; i < pts.length - 1; i++) {
        handles.push(makeHandle("medio", i));
        if (i > 0) { handles.push(makeHandle("punto", i)); }
      }
      positionHandles();
      drawLine();
      var h = focusIndex != null ? pointFor(focusIndex) : null;
      if (h) { h.el.focus(); }
    }

    function removePoint(i) {
      pts.splice(i, 1);
      build(i - 1 >= 1 ? i - 1 : null);
      save(0);
    }

    function addPoint(i) { // after pts[i], halfway to the next one
      pts.splice(i + 1, 0, middle(i));
      build(i + 1);
      return pointFor(i + 1);
    }

    function drag(h, e) {
      var el = h.el;
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* pointer already gone */ }
      el.focus({ preventScroll: true }); // so the keys (arrows, Supr) act on it next
      el.classList.add("activo");
      function move(ev) {
        if (ev.pointerId !== e.pointerId) { return; }
        var r = layer.getBoundingClientRect();
        pts[h.i] = [r2(inside((ev.clientX - r.left) / r.width * 100)), r2(inside((ev.clientY - r.top) / r.height * 100))];
        positionHandles();
        drawLine();
      }
      function up(ev) {
        if (ev.pointerId !== e.pointerId) { return; }
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
        el.classList.remove("activo");
        save(0);
      }
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
    }

    function makeHandle(kind, i) {
      var el = document.createElement("button");
      var h = { kind: kind, i: i, el: el };
      el.type = "button";
      el.className = kind === "punto" ? "ruta-punto" : "ruta-medio";
      el.setAttribute("aria-label", kind === "punto" ? "Punto del camino: flechas para moverlo, Supr para quitarlo"
                                                      : "Añadir un punto al camino aquí");
      el.title = kind === "punto" ? "Arrastra para moverlo · doble clic para quitarlo" : "Añadir un punto";
      el.addEventListener("pointerdown", function (e) {
        if (e.pointerType === "mouse" && e.button !== 0) { return; }
        // The handle, not the zoom window, gets this pointer.
        e.stopPropagation();
        e.preventDefault();
        drag(kind === "punto" ? h : addPoint(h.i), e);
      });
      el.addEventListener("click", function (e) {
        // Keyboard (Enter / space) on a "+": add the point and focus it.
        if (kind === "medio" && e.detail === 0) { addPoint(h.i); save(0); }
      });
      if (kind === "punto") {
        el.addEventListener("dblclick", function (e) { e.preventDefault(); e.stopPropagation(); removePoint(h.i); });
        el.addEventListener("contextmenu", function (e) { e.preventDefault(); removePoint(h.i); });
        el.addEventListener("keydown", function (e) {
          var step = e.shiftKey ? 1 : 0.2, p = pts[h.i];
          switch (e.key) {
            case "Delete": case "Backspace": removePoint(h.i); e.preventDefault(); return;
            case "ArrowLeft": pts[h.i] = [r2(inside(p[0] - step)), p[1]]; break;
            case "ArrowRight": pts[h.i] = [r2(inside(p[0] + step)), p[1]]; break;
            case "ArrowUp": pts[h.i] = [p[0], r2(inside(p[1] - step))]; break;
            case "ArrowDown": pts[h.i] = [p[0], r2(inside(p[1] + step))]; break;
            default: return;
          }
          e.preventDefault();
          positionHandles();
          drawLine();
          save(500);
        });
      }
      layer.appendChild(el);
      return h;
    }

    build(null);
  }

  // The note added from the board's form appears in the middle of what the window shows.
  $(document).on("mesa:enviar", "form[data-en-vista]", function () {
    var view = $($(this).data("en-vista"))[0];
    if (!view || !view.zoomCentro) { return; }
    var c = view.zoomCentro();
    $(this).find("[name=x]").val(Math.round(c.x - 100));
    $(this).find("[name=y]").val(Math.round(c.y - 60));
  });

  function initWindows(root) {
    $(root).find("[data-zoom]").addBack("[data-zoom]").each(function () { initZoom(this); });
    $(root).find(".mapa-lienzo").each(function () { initRouteEditor(this); });
  }
  // mesa.js is loaded at the end of <body>: the page is already there, so no need to wait
  // for jQuery's «ready» (which comes a moment later), and a quick first click works.
  initWindows(document);
  $(document).on("mesa:actualizado", function (e) { initWindows(e.target); });

  // ---- Ficha al día ----
  // Every few seconds the sheet asks for itself; the server answers with a fingerprint of
  // what it shows, and the panel is only replaced when it changed (the master took hit
  // points away from Turns, another device spent a use...). Never while someone is typing
  // in it, while another request is going on, or with the tab in the background.
  var $sheetFrame = $("#ficha-marco[data-refrescar]");
  if ($sheetFrame.length) {
    setInterval(function () {
      if (document.hidden || $.active > 0) { return; }
      var $panel = $("#ficha");
      var active = document.activeElement;
      if (active && $panel.has(active).length && $(active).is("input, textarea, select")) { return; }
      var typed = $panel.find("input[type=number], input[type=text], textarea").filter(function () {
        return this.value !== this.defaultValue;
      }).length;
      if (typed) { return; }
      $.ajax({ url: window.location.href, cache: false, dataType: "json" }).done(function (data) {
        if (!data || data.html === undefined || data.version === $sheetFrame.attr("data-version")) { return; }
        var $old = $("#ficha"), before = valuesOf($old), $new = $(data.html);
        keepOpen($old, $new);
        $old.replaceWith($new);
        highlightChanges($new, before);
        $new.trigger("mesa:actualizado");
        $sheetFrame.attr("data-version", data.version);
      });
    }, 6000);
  }

  // ---- Objetivos de una aventura ----
  // «Añadir objetivo» (or Enter in the box) turns the «new objective» box into a row
  // of the list, empties it and leaves it ready for the next one, as many times as
  // needed. The box is the last form of the formset: the new row takes its number
  // and the box moves on to the next. Without JavaScript the box is saved with «Guardar».
  function objectiveProgress($form) {
    var total = 0, done = 0;
    $form.find("#objetivos-lista [data-objetivo]").each(function () {
      if ($(this).find("input[name$='-DELETE']").prop("checked")) { return; }
      total += 1;
      if ($(this).find("input[name$='-done']").prop("checked")) { done += 1; }
    });
    $form.find("[data-objetivos-cuenta]").text(done + " / " + total);
    $form.find("[data-objetivos-progreso] .barra > span").css("width", (total ? Math.round(100 * done / total) : 0) + "%");
  }

  function addObjective($form) {
    var $box = $form.find("[data-objetivo-nuevo]");
    var $text = $box.find("input[name$='-text']");
    var text = $.trim($text.val());
    if (!text) { $text.trigger("focus"); return; }
    var $total = $form.find("input[name$='-TOTAL_FORMS']");
    var index = +$total.val() - 1; // the box's number
    var html = $.trim($("#objetivo-plantilla").html().replace(/__prefix__/g, index));
    var $row = $($.parseHTML(html));
    $row.find("input[name$='-text']").val(text);
    $row.find("input[name$='-hidden']").prop("checked", $box.find("input[name$='-hidden']").prop("checked"));
    $form.find("#objetivos-lista").append($row);
    var from = "-" + index + "-", to = "-" + (index + 1) + "-";
    $box.find("[name], [id], label[for]").each(function () {
      var el = this;
      ["name", "id", "for"].forEach(function (attr) {
        var value = el.getAttribute(attr);
        if (value) { el.setAttribute(attr, value.replace(from, to)); }
      });
    });
    $total.val(index + 2);
    $text.val("");
    $box.find("input[name$='-hidden']").prop("checked", false);
    $text.trigger("focus");
    objectiveProgress($form);
  }

  $("[data-anadir-objetivo]").prop("hidden", false);
  $(document).on("click", "[data-anadir-objetivo]", function () { addObjective($(this).closest("form")); });
  $(document).on("keydown", "[data-objetivo-nuevo] input[name$='-text']", function (event) {
    if (event.key === "Enter") { event.preventDefault(); addObjective($(this).closest("form")); }
  });
  $(document).on("change", "form[data-objetivos] #objetivos-lista input[type=checkbox]", function () {
    objectiveProgress($(this).closest("form"));
  });

  // ---- Colocar en el corcho o en el mapa ----
  // A click on the surface fills the x/y fields of the form and sends it.
  $(document).on("click", "[data-colocar]", function (event) {
    var $surface = $(this);
    var $form = $($surface.data("colocar"));
    if (!$form.length || $(event.target).closest("a, button").length) { return; }
    // On the map, positions are relative to the (maybe zoomed) layer that holds the image.
    var rect = ($surface.children(".mapa-lienzo")[0] || this).getBoundingClientRect();
    var mode = $surface.data("unidad"); // "px" (board, data-ancho x data-alto) or "%" (map)
    var boardW = +$surface.data("ancho") || 1100, boardH = +$surface.data("alto") || 640;
    var x, y;
    if (mode === "%") {
      x = ((event.clientX - rect.left) / rect.width) * 100;
      y = ((event.clientY - rect.top) / rect.height) * 100;
      x = Math.round(Math.max(2, Math.min(98, x)) * 10) / 10;
      y = Math.round(Math.max(2, Math.min(98, y)) * 10) / 10;
    } else {
      // The rect is the scaled board on screen, so this works at any zoom. The note's top centre goes there.
      x = Math.round((event.clientX - rect.left) * (boardW / rect.width) - 100);
      y = Math.round((event.clientY - rect.top) * (boardH / rect.height) - 20);
      x = Math.max(8, Math.min(boardW - 208, x));
      y = Math.max(8, Math.min(boardH - 128, y));
    }
    $form.find("[name=x]").val(x);
    $form.find("[name=y]").val(y);
    if ($form.data("enviar")) { $form.trigger("submit"); }
    else { $form.trigger("mesa:colocado"); $form.find("input[type=text]").first().trigger("focus"); }
  });

  // ---- Mapa: capa del tiempo ----
  // «Tiempo real» loads the layer by AJAX (the server caches Open-Meteo for 20 min) and
  // draws it on the map; the browser remembers whether it was on. Without JavaScript the
  // link opens the map with ?tiempo=1 and the server draws it.
  var weatherTimer = null;
  function weatherWanted() {
    try { return window.localStorage.getItem("tiempo:" + window.location.pathname) === "1"; } catch (e) { return false; }
  }
  function rememberWeather(on) {
    try { window.localStorage.setItem("tiempo:" + window.location.pathname, on ? "1" : "0"); } catch (e) { /* not kept */ }
  }
  function loadWeather($toggle) {
    $.get($toggle.data("tiempo")).done(function (data) {
      var $lienzo = $(".mapa .mapa-lienzo").first();
      $lienzo.find(".capa-tiempo").remove();
      $lienzo.children("img, .vacio").first().after(data.layer);
      $("[data-tiempo-pie]").html(data.caption);
    });
  }
  function showWeather($toggle, on) {
    $toggle.attr("aria-pressed", on ? "true" : "false");
    clearInterval(weatherTimer);
    if (on) {
      loadWeather($toggle);
      weatherTimer = setInterval(function () { loadWeather($toggle); }, 10 * 60 * 1000);
    } else {
      $(".mapa .capa-tiempo").remove();
      $("[data-tiempo-pie]").empty();
    }
  }
  $(document).on("click", "[data-tiempo]", function (event) {
    event.preventDefault();
    var on = $(this).attr("aria-pressed") !== "true";
    rememberWeather(on);
    showWeather($(this), on);
  });
  function restoreWeather() {
    var $toggle = $("[data-tiempo]");
    // Already drawn by the server (?tiempo=1, or the master editing regions): only keep it fresh.
    if ($toggle.length && weatherWanted() && !$(".mapa .capa-tiempo").length) { showWeather($toggle, true); }
  }
  restoreWeather();
  $(document).on("mesa:actualizado", restoreWeather);

  // The master draws a region: each click on the map adds a corner (x,y in % of the image).
  function drawingPoints($form) {
    var value = $.trim($form.find("[name=points]").val());
    return value ? value.split(/\s+/) : [];
  }
  function showDrawing($form, points) {
    $form.find("[name=points]").val(points.join(" "));
    $(".mapa [data-dibujo]").attr("points", points.join(" "));
    var text = $form.find("[data-dibujo-cuenta]");
    text.text(points.length ? points.length + (points.length === 1 ? " punto" : " puntos") + (points.length < 3 ? " (mínimo 3)" : "") : "Sin puntos todavía.");
  }
  $(document).on("click", "[data-dibujar]", function (event) {
    var $form = $($(this).data("dibujar"));
    if (!$form.length || $(event.target).closest("a, button").length || $form.find("[name=location]").val()) { return; }
    var rect = $(this).children(".mapa-lienzo")[0].getBoundingClientRect();
    var x = Math.round(Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)) * 10) / 10;
    var y = Math.round(Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)) * 10) / 10;
    var points = drawingPoints($form);
    points.push(x + "," + y);
    showDrawing($form, points);
  });
  $(document).on("click", "[data-dibujo-deshacer]", function () {
    var $form = $(this).closest("form"), points = drawingPoints($form);
    points.pop();
    showDrawing($form, points);
  });
  $(document).on("click", "[data-dibujo-borrar]", function () { showDrawing($(this).closest("form"), []); });
  $(document).on("change", "#form-region [name=location]", function () {
    $(this).closest("form").find("[data-dibujo-controles]").toggle(!$(this).val());
  });

  // The real place: suggestions from Open-Meteo's geocoding while the master types.
  var placeTimer = null;
  $(document).on("input", "[data-buscar-lugar]", function () {
    var $input = $(this), $form = $input.closest("form"), $list = $form.find("[data-sugerencias]");
    $form.find("[name=latitude], [name=longitude]").val("");  // typed again: the chosen place no longer applies
    clearTimeout(placeTimer);
    var text = $.trim($input.val());
    if (text.length < 3) { $list.empty(); return; }
    placeTimer = setTimeout(function () {
      $.get($input.data("buscar-lugar"), { q: text }).done(function (data) {
        $list.empty();
        if (data.error) { $("<li class='pequeno error'>").text(data.error).appendTo($list); return; }
        $.each(data.results, function (i, place) {
          $("<button type='button' class='btn btn-texto btn-pequeno'>").text(place.name).data("lugar", place)
            .appendTo($("<li>").appendTo($list));
        });
      });
    }, 400);
  });
  $(document).on("click", "[data-sugerencias] button", function () {
    var place = $(this).data("lugar"), $form = $(this).closest("form");
    $form.find("[data-buscar-lugar]").val(place.name);
    $form.find("[name=latitude]").val(place.latitude);
    $form.find("[name=longitude]").val(place.longitude);
    $(this).closest("[data-sugerencias]").empty();
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
