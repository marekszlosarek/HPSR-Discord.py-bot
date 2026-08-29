import matplotlib.pyplot as plt
import io


def get_graph(theRunData) -> bytes:
    splits = [(0,0)]
    golds = []

    for split in theRunData['splits']:
        pb = split['comparisons']['Personal Best']
        current = split['splitTime']
        gold = split['bestPossible']
        if current:
            if (not gold) or (gold > current-splits[-1][0]*1000):
                golds.append(len(splits)-1)
            if pb:
                splits.append((current/1000, (pb-current)/1000))

    graph = make_graph(splits, golds)

    return graph.getvalue()



def make_graph(points, golds=[]) -> io.BytesIO:
    # Create the plot
    fig, ax = plt.subplots()

    # Plot the lines
    for i in range(len(points) - 1):
        x_values = [points[i][0], points[i+1][0]]
        y_values = [points[i][1], points[i+1][1]]

        ax.plot(x_values, y_values, color='black')

    updated_points = [(0, 0)]

    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]

        x_start, y_start = p1
        x_end, y_end = p2

        is_gold = i in golds

        # If the segment crosses zero, split it at the intersection
        if y_start * y_end < 0:
            x_intersect = (
                x_start * y_end - x_end * y_start
            ) / (y_end - y_start)

            # First half
            fill_color = 'gold' if is_gold else (
                'green' if y_start >= 0 else 'red'
            )

            ax.fill_between(
                [x_start, x_intersect],
                [y_start, 0],
                0,
                color=fill_color,
                alpha=0.7
            )

            # Second half
            fill_color = 'gold' if is_gold else (
                'green' if y_end >= 0 else 'red'
            )

            ax.fill_between(
                [x_intersect, x_end],
                [0, y_end],
                0,
                color=fill_color,
                alpha=0.7
            )

        else:
            if is_gold:
                fill_color = 'gold'
            elif y_start >= 0 and y_end >= 0:
                fill_color = 'green'
            elif y_start <= 0 and y_end <= 0:
                fill_color = 'red'
            else:
                fill_color = 'gold'

            ax.fill_between(
                [x_start, x_end],
                [y_start, y_end],
                0,
                color=fill_color,
                alpha=0.7
            )


    for i in range(len(updated_points) - 1):
        x_start, y_start = updated_points[i]
        x_end, y_end = updated_points[i+1]
        
        # Determine fill color
        if y_start >= 0 and y_end >= 0:
            fill_color = 'green'
        elif y_start <= 0 and y_end <= 0:
            fill_color = 'red'
        else:
            fill_color = 'gold'
        
        # Fill the area between the points and the x-axis
        ax.fill_between([x_start, x_end], [y_start, y_end], 0, color=fill_color, alpha=0.7)

    dot_scale = max(0.25, 1 - len(points) / 100)
    # Plot the points
    for x, y in points:
        if y >= 0:
            point_color = 'green'
        elif y < 0:
            point_color = 'red'
        ax.plot(x, y, 'o', color=point_color, markersize=6 * dot_scale)

    # Add grid and axes lines
    plt.axhline(0, color='black', linewidth=0.5)
    plt.xlim(0,x)  # Set x-axis lower limit to 0
    plt.xticks([])  # Hide x-axis ticks
    plt.yticks([])  # Hide y-axis ticks
    plt.gca().spines['right'].set_visible(False)  # Hide right spine
    plt.gca().spines['top'].set_visible(False)  # Hide top spine
    plt.gca().spines['left'].set_visible(False)  # Hide right spine
    plt.gca().spines['bottom'].set_visible(False)  # Hide top spine
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', transparent=True)
    plt.close()
    buffer.seek(0)

    return buffer


if __name__ == "__main__":
    import requests

    username = "flo203"
    url = f"https://therun.gg/api/live/{username}"

    response = requests.get(url)
    response.raise_for_status()

    data = response.json()

    graph = get_graph(data)

    with open("pace_graph.png", "wb") as f:
        f.write(graph)

    print("Graph saved to pace_graph.png")