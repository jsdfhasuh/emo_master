## ADDED Requirements

### Requirement: Configurable shared host header
The platform SHALL persist project-owned logo dimensions, header surface and typography, and start/stop labels and appearance in the shared page shell. Qt and Web SHALL render those properties while retaining existing host commands, targets, permissions and enabled state.

#### Scenario: Author and publish a branded shell
- **WHEN** an author sets logo dimensions, header typography and both control labels/styles, saves and publishes the page
- **THEN** the editable document, published package, Qt host and Web host retain and use the configured values, and author previews display the same properties without starting or stopping tasks

### Requirement: State-driven title and marker
The existing indicator SHALL allow its title color to follow the mapped state and its optional marker to appear before the title. Static title color SHALL remain effective when state-following is disabled.

#### Scenario: A result changes and later becomes empty
- **WHEN** a configured indicator receives mapped values and then an empty or unmapped value
- **THEN** its title, marker, value and row surface update from the same current state and no previous active marker or color remains

### Requirement: Independent history control visibility
History table and list views SHALL independently control filter, sort and pagination visibility without changing the query source or record selection mechanism. Changing view mode SHALL retain those settings.

#### Scenario: Review records with hidden built-in bars
- **WHEN** all three controls are hidden and an operator uses external previous, next and first-record actions
- **THEN** adjacent-record actions cross data page boundaries, first-record action reloads the latest records, and dependent images and values follow the selected record

### Requirement: Generic line from two selected points
The platform SHALL provide a generic operator that accepts two selected point lists and a current frame, producing the existing line collection contract. It SHALL not select business classes or reuse a previous line when an endpoint is absent.

#### Scenario: Render selected centers as a dashed line
- **WHEN** each input contains one point in the current frame's coordinate space
- **THEN** the output contains one line usable by the existing Annotate dashed-line and endpoint-marker options

#### Scenario: A selected point is absent
- **WHEN** either current endpoint list is empty
- **THEN** the operator returns an empty line collection in the current frame rather than the preceding result's line

### Requirement: Distinguishable run selectors
The Web host and runtime preview SHALL include an identity fragment in run labels and expose the full identity in a tooltip while retaining exact run IDs as selection values.

#### Scenario: The same workflow has multiple runs
- **WHEN** multiple runs have the same workflow name and status
- **THEN** the view, stop-target and preview selectors distinguish their identities without changing the selected command target
