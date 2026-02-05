addEvent(window, "load", sortTablesInit);

var SortTablesIndex; // Column index to sort on

function sortTablesInit() { // Find all tables with class sortable and make them sortable
  if (!document.getElementsByTagName) return; // Nothing to do
  var tables = document.getElementsByTagName("table"); // Get all tables
  for (var i = 0; i < tables.length; ++i) { // Loop over all tables in the document
    table = tables[i];
    if ((table.className.indexOf("sortable") != -1) && (table.id)) { // class=sortable id=something
      makeTableSortable(table); // Add sort functionality to this table
    }
  }
}

function getText(elem) {
  if (typeof elem == "string") return elem;
  if (typeof elem == "undefined") return elem;
  if (elem.innerText) return elem.innerText;

  var str = "";
  var children = elem.childNodes;
  var n = children.length;
  for (var i =0; i < n; ++i) {
    switch (children[i].nodeType) {
      case 1: // ELEMENT_NODE
        str += getText(children[i]);
        break;
      case 3: // TEXT_NODE
        str += children[i].nodeValue;
        break;
    }
  }
  return str;
}

function addOnClick(table, rows, formats) {
  for (var i = 0; i < rows.length; ++i) { // Loop over rows in the head
    var cells = rows[i].cells;
    for (var j = 0; j < cells.length; ++j) {
      var cell = cells[j];
      var format = (j == 0) ? "date" : "numeric";
      cell.setAttribute("onclick", "sortTable('" + table.id + "'," + j + ",'" + format + "');");
      if (i == 0) {
        cell.setAttribute("sort:dir", "up");
        cell.innerHTML += "<span></span>";
      }
    }
  }
}

function makeTableSortable(table) {
  var thead = table.tHead;
  var tfoot = table.tFoot;
  var tbodies = table.tBodies;

  if (!thead || !thead.rows || !thead.rows.length) return; // No thead
  if (!tbodies || !tbodies.length) return; // Not exactly 1 tbody

  var nRows = 0;
  for (var i = 0; i < tbodies.length; ++i) {
    var tbody = tbodies[i];
    if (tbody.rows && tbody.rows.length) {
      nRows += tbody.rows.length;
      if (nRows > 1) break;
    }
  }

  if (nRows <= 1) return; // No reason to sort

  addOnClick(table, thead.rows);
  if (tfoot && tfoot.rows) addOnClick(table, tfoot.rows);
}

var sortTableText;

function textSorter(a, b) { 
    aa = sortTableText[a];
    bb = sortTableText[b];

    return (aa < bb) ? -1 : (aa > bb) ? 1 : 0;
}

function numericSorter(a, b) { 
    aa = sortTableText[a];
    bb = sortTableText[b];
    return aa - bb;
}

function makeText(a, format) {
  if (format == "numeric") {
    var aa = parseFloat(a);
    return isNaN(aa) ? 0 : aa;
  }

  if (format == "date") { return Date.parse(a); }

  return a.toLowerCase(); 
}

function sortTableArrows(th, column, direction) {
  if (th && th.rows && th.rows.length) {
    var cells = th.rows[0].cells;
    for (var i = 0; i < cells.length; ++i) {
      var spans = cells[i].getElementsByTagName("span");
      if (i != column) {
        spans[spans.length - 1].innerHTML = "";
      } else {
        spans[spans.length - 1].innerHTML = "&nbsp;" + (direction == "up" ? "&darr;" : "&uarr;");
      }
    } 
  } 
}

function sortTable(id, column, format) {
  var table = document.getElementById(id); // Get the table to operate on
  var thead = table.tHead;
  var tbodies = table.tBodies;
  var headCell = table.tHead.rows[0].cells[column];
  var direction = headCell.getAttribute("sort:dir");

  for (var i = 0; i < tbodies.length; ++i) { // Sort each tbody
    var indices = new Array();
    var rowCopy = new Array();
    sortTableText = new Array();
    var tbody = table.tBodies[0];
    var rows = tbody.rows;

    for (var j = 0; j < rows.length; ++j) {
      var row = rows[j];
      indices.push(j); // Index within table
      sortTableText.push(makeText(getText(row.cells[column]), format));
      rowCopy.push(row);
    }

    if ((format == "numeric") || (format == "date")) {indices.sort(numericSorter);
    } else {indices.sort(textSorter);}

    if (direction == "down") {
      for (var i = 0; i < indices.length; ++i) { 
        tbody.appendChild(rowCopy[indices[indices.length - i - 1]]); 
      }
    } else {
      for (var i = 0; i < indices.length; ++i) { tbody.appendChild(rowCopy[indices[i]]); }
    }
  }

  headCell.setAttribute("sort:dir", direction == "down" ? "up" : "down");
  sortTableArrows(thead, column, direction);
  sortTableArrows(table.tFoot, column, direction);

  return false;
}

function addEvent(elm, evType, fn, useCapture)
// addEvent and removeEvent
// cross-browser event handling for IE5+,  NS6 and Mozilla
// By Scott Andrew
{
  if (elm.addEventListener) {
    elm.addEventListener(evType, fn, useCapture);
    return true;
  } else if (elm.attachEvent) {
    var r = elm.attachEvent("on"+evType, fn);
    return r;
  } else {
    alert("Handler could not be removed");
  }
} 
