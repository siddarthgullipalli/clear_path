/**
 * ShipGuard OpenUI Component Library
 *
 * Implements the OpenUI Lang defineComponent + createLibrary pattern.
 * The library owns:
 *   - The canonical RiskCard prop schema
 *   - The system-prompt generator (library.prompt())
 *   - The stream parser that rebuilds RiskCard instances from LLM token chunks
 *   - The render function that produces HTML matching the dashboard CSS
 *
 * No external dependencies. Exposed as window.ShipGuardOpenUI.
 */
(function (global) {
  'use strict';

  // ── Core API ────────────────────────────────────────────────────────────────

  function defineComponent(name, schema) {
    return {
      name,
      props:        schema.props        || [],
      contentSlots: schema.contentSlots || [],
      render:       schema.render,
    };
  }

  function createLibrary(libraryName, components) {
    const map = {};
    components.forEach(function (c) { map[c.name] = c; });

    return {
      name:       libraryName,
      components: map,
      /** Returns the system-prompt string to paste into Pioneer's score_and_reason node. */
      prompt:         function ()            { return buildPrompt(libraryName, components); },
      /** Parses a complete OpenUI Lang source string into [{component, props}]. */
      parse:          function (src)         { return parseSource(src, map); },
      /** Returns a StreamRenderer that accepts incremental token chunks. */
      createRenderer: function (container)   { return new StreamRenderer(container, map); },
    };
  }

  // ── System-prompt builder ───────────────────────────────────────────────────

  function buildPrompt(libraryName, components) {
    var lines = [
      'You are the ' + libraryName + ' risk reasoning engine.',
      'Output each shipment risk assessment as an OpenUI Lang component block.',
      'Emit ALL five shipments sequentially. No preamble, no explanation, no markdown.',
      '',
      '─── COMPONENT SCHEMA ─────────────────────────────────────────────────────',
      '',
    ];

    components.forEach(function (comp) {
      lines.push('<' + comp.name);
      comp.props.forEach(function (p) {
        var typeDesc = p.enum ? ('enum: ' + p.enum.join(' | ')) : p.type;
        var req      = p.required ? ' [required]' : ' [optional]';
        lines.push('  ' + p.name + '="' + typeDesc + '"' + req);
      });
      lines.push('>');
      if (comp.contentSlots.length === 1) {
        lines.push('{' + comp.contentSlots[0] + ' — plain text, no markup}');
      } else if (comp.contentSlots.length > 1) {
        comp.contentSlots.forEach(function (s, i) {
          lines.push('{slot ' + (i + 1) + ': ' + s + ' — plain text, no markup}');
        });
        lines.push('(slots separated by the literal string "||" on its own line)');
      }
      lines.push('</' + comp.name + '>');
      lines.push('');
    });

    lines.push('─── RULES ────────────────────────────────────────────────────────────────');
    lines.push('');
    lines.push('severity classification (highest bracket where EITHER condition is met):');
    lines.push('  CRITICAL  wind_knots_max_72h ≥ 75  OR  storm_probability ≥ 0.80');
    lines.push('  HIGH      wind_knots_max_72h ≥ 60  OR  storm_probability ≥ 0.60');
    lines.push('  MEDIUM    wind_knots_max_72h ≥ 45  OR  storm_probability ≥ 0.35');
    lines.push('  LOW       otherwise');
    lines.push('');
    lines.push('status rules:');
    lines.push('  DIVERTED  when severity = CRITICAL');
    lines.push('  DELAYED   when severity = HIGH or MEDIUM');
    lines.push('  IN_TRANSIT when severity = LOW');
    lines.push('');
    lines.push('wind / storm / wave: use the highest-risk waypoint on each route');
    lines.push('eta_hours: additional hours added due to rerouting (0 if no reroute)');
    lines.push('alternate_route slot: provide route text for HIGH/CRITICAL; use "null" for LOW/MEDIUM');
    lines.push('');
    lines.push('─── EXAMPLE OUTPUT ───────────────────────────────────────────────────────');
    lines.push('');
    lines.push('<RiskCard vessel="MV Pacific Star" severity="CRITICAL" origin="Taipei"');
    lines.push('  destination="Rotterdam" cargo="Semiconductors" status="DIVERTED"');
    lines.push('  wind="91" storm="0.94" wave="8.5" eta_hours="168">');
    lines.push('Typhoon-force winds of 91 knots forecast at waypoint (20.0N, 118.0E) within');
    lines.push('72 hours with 94% storm probability. Immediate diversion required to protect');
    lines.push('$42M semiconductor cargo — continuing risks vessel safety and total cargo loss.');
    lines.push('||');
    lines.push('Via Cape of Good Hope (bypass Suez)');
    lines.push('</RiskCard>');
    lines.push('');
    lines.push('─── END OF SCHEMA ────────────────────────────────────────────────────────');

    return lines.join('\n');
  }

  // ── OpenUI Lang parser ──────────────────────────────────────────────────────
  // Parses full or partial source text into component instances.
  // Handles the slot separator "||" for multi-slot content.

  function parseAttrs(attrStr) {
    var props  = {};
    var re     = /(\w+)="([^"]*)"/g;
    var m;
    while ((m = re.exec(attrStr)) !== null) {
      props[m[1]] = m[2];
    }
    return props;
  }

  function parseSource(src, componentMap) {
    var results = [];
    // Matches <ComponentName ...attrs...>content</ComponentName>
    var re = /<(\w+)([\s\S]*?)>([\s\S]*?)<\/\1>/g;
    var m;
    while ((m = re.exec(src)) !== null) {
      var tagName = m[1];
      var comp    = componentMap[tagName];
      if (!comp) continue;

      var props   = parseAttrs(m[2]);
      var content = m[3].trim();
      var slots   = content.split(/\|\|/);

      comp.contentSlots.forEach(function (slotName, i) {
        var raw = slots[i] ? slots[i].trim() : null;
        props[slotName] = (raw === 'null' || raw === '') ? null : raw;
      });

      results.push({ component: comp, props: props });
    }
    return results;
  }

  // ── StreamRenderer ──────────────────────────────────────────────────────────
  // Accepts incremental text chunks from an SSE stream.
  // Emits a fully-rendered card DOM node each time a complete block is parsed.

  function StreamRenderer(container, componentMap) {
    this.container      = container;
    this.componentMap   = componentMap;
    this.buffer         = '';
    this.renderedCount  = 0;
    this.prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.onCard         = null; // optional callback(cardEl, props)
  }

  StreamRenderer.prototype.push = function (chunk) {
    this.buffer += chunk;
    this._tryFlush();
  };

  StreamRenderer.prototype._tryFlush = function () {
    var self = this;
    var madeProgress = true;
    while (madeProgress) {
      madeProgress = false;
      Object.keys(this.componentMap).forEach(function (name) {
        var closeTag = '</' + name + '>';
        var closeIdx = self.buffer.indexOf(closeTag);
        if (closeIdx === -1) return;

        var afterClose = closeIdx + closeTag.length;
        var openIdx    = self.buffer.lastIndexOf('<' + name, closeIdx);
        if (openIdx === -1) return;

        var block      = self.buffer.slice(openIdx, afterClose);
        self.buffer    = self.buffer.slice(afterClose);

        var parsed = parseSource(block, self.componentMap);
        if (parsed.length > 0) {
          self._mountCard(parsed[0]);
          madeProgress = true;
        }
      });
    }
  };

  StreamRenderer.prototype._mountCard = function (parsed) {
    var html    = parsed.component.render(parsed.props);
    var wrapper = document.createElement('div');
    wrapper.innerHTML = html.trim();
    var card = wrapper.firstElementChild;
    if (!card) return;

    card.setAttribute('data-source', 'openui');

    if (!this.prefersReduced) {
      card.style.opacity   = '0';
      card.style.transform = 'translateY(8px)';
    }

    this.container.appendChild(card);
    this.renderedCount++;

    if (!this.prefersReduced) {
      requestAnimationFrame(function () {
        card.style.transition = 'opacity 280ms cubic-bezier(0.16,1,0.3,1), transform 280ms cubic-bezier(0.16,1,0.3,1)';
        card.style.opacity    = '1';
        card.style.transform  = 'translateY(0)';
      });
    }

    if (typeof this.onCard === 'function') this.onCard(card, parsed.props);
  };

  StreamRenderer.prototype.flush = function () {
    if (this.buffer.trim()) this._tryFlush();
  };

  // ── Design tokens (mirrors index.html CSS) ─────────────────────────────────

  var SEV = {
    CRITICAL: { text: '#f85149', bg: 'rgba(248,81,73,0.13)',  border: 'rgba(248,81,73,0.24)', left: 'rgba(248,81,73,0.50)' },
    HIGH:     { text: '#fb8500', bg: 'rgba(251,133,0,0.11)',  border: 'rgba(251,133,0,0.22)', left: 'rgba(251,133,0,0.40)' },
    MEDIUM:   { text: '#d29922', bg: 'rgba(210,153,34,0.11)', border: 'rgba(210,153,34,0.22)',left: null },
    LOW:      { text: '#3fb950', bg: 'rgba(63,185,80,0.09)',  border: 'rgba(63,185,80,0.18)', left: null },
  };

  var STA = {
    IN_TRANSIT: { bg: 'rgba(56,139,253,0.11)',  text: '#388bfd' },
    DIVERTED:   { bg: 'rgba(137,87,229,0.11)',  text: '#8957e5' },
    DELAYED:    { bg: 'rgba(110,118,129,0.14)', text: '#768390' },
  };

  function wClass(v)  { return v >= 75 ? 'danger' : v >= 60 ? 'warn' : ''; }
  function sClass(pct){ return pct >= 80 ? 'danger' : pct >= 60 ? 'warn' : ''; }

  function etaLabel(hrs) {
    if (!hrs || hrs <= 0) return null;
    return hrs < 24 ? ('+' + hrs + 'h') : ('+' + Math.round(hrs / 24) + 'd delay');
  }

  // ── RiskCard component ──────────────────────────────────────────────────────

  var RiskCard = defineComponent('RiskCard', {
    props: [
      { name: 'vessel',      type: 'string', required: true  },
      { name: 'severity',    type: 'string', required: true,  enum: ['LOW','MEDIUM','HIGH','CRITICAL'] },
      { name: 'origin',      type: 'string', required: true  },
      { name: 'destination', type: 'string', required: true  },
      { name: 'cargo',       type: 'string', required: true  },
      { name: 'status',      type: 'string', required: true,  enum: ['IN_TRANSIT','DIVERTED','DELAYED'] },
      { name: 'wind',        type: 'number', required: true  },
      { name: 'storm',       type: 'number', required: true  },
      { name: 'wave',        type: 'number', required: true  },
      { name: 'eta_hours',   type: 'number', required: false },
    ],
    contentSlots: ['reasoning', 'alternate_route'],

    render: function (props) {
      var sev    = props.severity || 'LOW';
      var status = props.status   || 'IN_TRANSIT';
      var sc     = SEV[sev]    || SEV.LOW;
      var stc    = STA[status] || STA.IN_TRANSIT;

      var wind     = parseFloat(props.wind)     || 0;
      var storm    = parseFloat(props.storm)    || 0;
      var stormPct = storm <= 1 ? storm * 100 : storm; // accept 0.94 or 94
      var wave     = parseFloat(props.wave)     || 0;
      var etaHrs   = parseInt(props.eta_hours)  || 0;

      var wc     = wClass(wind);
      var scc    = sClass(stormPct);
      var etaTxt = etaLabel(etaHrs);

      var showAlt = (sev === 'CRITICAL' || sev === 'HIGH')
                 && props.alternate_route
                 && props.alternate_route !== 'null';

      var leftStyle = sc.left ? ('border-left:2px solid ' + sc.left + ';') : '';
      var statusLabel = status.replace(/_/g, ' ');

      var altHTML = '';
      if (showAlt) {
        var altBg     = sev === 'CRITICAL' ? 'rgba(248,81,73,0.05)'  : 'rgba(251,133,0,0.05)';
        var altBorder = sev === 'CRITICAL' ? 'rgba(248,81,73,0.18)'  : 'rgba(251,133,0,0.18)';
        altHTML = [
          '<div style="display:flex;flex-direction:column;gap:6px;padding:10px 12px;border-radius:5px;',
          'background:', altBg, ';border:0.5px solid ', altBorder, '">',
          '<div style="display:flex;align-items:center;justify-content:space-between">',
          '<span style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#8b949e">Alternate Route</span>',
          etaTxt ? '<span style="font-size:11px;font-weight:600;color:' + sc.text + '">' + etaTxt + '</span>' : '',
          '</div>',
          '<span style="font-size:12px;color:#e6edf3;line-height:1.4">', props.alternate_route, '</span>',
          '</div>',
        ].join('');
      }

      var reasonHTML = (props.reasoning && props.reasoning !== 'null')
        ? '<p style="font-size:12px;color:#8b949e;line-height:1.6;max-width:65ch;margin:0">'
          + props.reasoning + '</p>'
        : '';

      return [
        '<article class="card" data-severity="', sev, '" style="', leftStyle, '">',

        '<div class="card-top">',
        '<span class="vessel-name">', props.vessel, '</span>',
        '<span class="sev-badge ', sev, '"><span class="sev-dot"></span>', sev, '</span>',
        '</div>',

        '<div class="card-route-row">',
        '<span class="route-label">', props.origin,
          '<span class="route-arrow"> &rarr; </span>', props.destination,
        '</span>',
        '<span class="status-pill ', status, '">', statusLabel, '</span>',
        '</div>',

        '<div class="metrics-grid">',

        '<div class="metric-tile">',
        '<span class="metric-label">Max Wind 72hr</span>',
        '<span class="metric-value ', wc, '">',
        '<span>', Math.round(wind), '</span>',
        '<span class="metric-unit">kn</span></span>',
        '</div>',

        '<div class="metric-tile">',
        '<span class="metric-label">Storm Probability</span>',
        '<span class="metric-value ', scc, '">',
        '<span>', Math.round(stormPct), '</span>',
        '<span class="metric-unit">%</span></span>',
        '</div>',

        '<div class="metric-tile">',
        '<span class="metric-label">Wave Height</span>',
        '<span class="metric-value">',
        '<span>', wave.toFixed(1), '</span>',
        '<span class="metric-unit">m</span></span>',
        '</div>',

        '<div class="metric-tile">',
        '<span class="metric-label">Cargo</span>',
        '<span class="metric-value cargo">', props.cargo, '</span>',
        '</div>',

        '</div>', // metrics-grid

        reasonHTML,
        altHTML,

        '</article>',
      ].join('');
    },
  });

  // ── Instantiate library and expose globally ─────────────────────────────────

  var library = createLibrary('ShipGuard', [RiskCard]);

  global.ShipGuardOpenUI = {
    defineComponent: defineComponent,
    createLibrary:   createLibrary,
    library:         library,
    RiskCard:        RiskCard,
  };

}(window));
